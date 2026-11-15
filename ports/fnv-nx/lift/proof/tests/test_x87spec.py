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
    R = z3.K(z3.BitVecSort(3), K(0, 80)); top = (8 - len(vals)) & 7; tag = 0xFFFF
    for i, v in enumerate(vals):
        if isinstance(v, tuple): img = v[1]                                   # ('img', n): a raw 80-bit image
        else: img = val(X.to_m80(K(f2b(v) if isinstance(v, float) else v, 64)))
        p = (top + i) & 7
        R = z3.Store(R, K(p, 3), K(img, 80))
        tag = (tag & ~(3 << 2 * p)) | (val(X.tag_of_img(K(img, 80))) << 2 * p)
    return X.St(R=R, tag=K(tag, 16), fsw=K(top << 11, 16), fcw=K(fcw, 16), fip=K(0, 32), fcs=K(0, 16), fdp=K(0, 32), fds=K(0, 16), fop=K(0, 11))

def rd(s, p):
    """physical register p of s read as binary64 (the arithmetic-class view under the default control word)"""
    return val(X.view64(z3.Select(s.R, K(p, 3)), K(0x037F, 16)))

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
    def test_tag_of_img(self):
        # SDM 1:8.1.7 classes of the 80-bit image: zero 01; NaN, infinity, denormal, unsupported 10; the rest 00
        for img, name in ((0x3FFF8000000000000000, 'valid'), (0, 'zero'), (0x80000000000000000000, 'zero'), (0x7FFF8000000000000000, 'special'),
                          (0x7FFFC000000000000000, 'special'), (1, 'special'), (0x00008000000000000000, 'special'),     # denormal, pseudo-denormal
                          (0x3FFF0000000000000001, 'special'), (0x7FFF0000000000000000, 'special'),                      # unnormal, pseudo-infinity
                          (0xFFFF0000000000000000 | 5, 'special')):                                                      # pseudo-NaN (the MMX ones in the exponent)
            self.assertEqual(val(X.tag_of_img(K(img, 80))), {'valid': 0, 'zero': 1, 'special': 2}[name], hex(img))
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
    ST = {'nan': NAN, 'normal': 1.5, 'infinity': float('inf'), 'zero': 0.0, 'denormal': ('img', 1), 'unsupported': ('img', 0xFFFF0000000000000001)}   # 80-bit denormal; an MMX write of 1 (exponent 7FFF, integer bit 0)
    def test_classes(self):
        for cls, c3, c2, c0, _ in rows('fxam'):
            s = state([]) if cls == 'empty' else state([self.ST[cls]])
            self.assertEqual(ccs(run(('d9', 'reg', 4, 5), s).s.fsw), [int(c3), int(c2), int(c0)], cls)
    def test_c1_is_sign_even_when_empty(self):
        for v, sign in ((-1.5, 1), (1.5, 0), (-0.0, 1)):
            self.assertEqual((val(run(('d9', 'reg', 4, 5), state([v])).s.fsw) >> X.C1) & 1, sign)
        s = state([]); s = s.copy(R=z3.Store(s.R, K(0, 3), K(val(X.to_m80(K(f2b(-1.0), 64))), 80)))                    # empty register that holds a negative pattern: C1 = sign (SDM 2A:FXAM)
        self.assertEqual((val(run(('d9', 'reg', 4, 5), s).s.fsw) >> X.C1) & 1, 1)
class Formats(unittest.TestCase):
    EXACT = {'+0': 0.0, '-0': -0.0, '+inf': float('inf'), '-inf': float('-inf'), '+1.0': 1.0, '-2.0': -2.0}
    def test_m80_constants(self):
        for name, hexv, _ in rows('m80'):
            if name in self.EXACT:
                b = K(f2b(self.EXACT[name]), 64)
                self.assertEqual(val(X.to_m80(b)), int(hexv, 16), name)
                self.assertEqual(val(X.view64(K(int(hexv, 16), 80), K(0x037F, 16))), f2b(self.EXACT[name]), name)
            if name == 'qnan_indefinite':
                self.assertEqual(val(X.to_m80(K(X.QNAN_INDEF, 64))), int(hexv, 16))
                self.assertEqual(val(X.view64(K(int(hexv, 16), 80), K(0x037F, 16))), X.QNAN_INDEF)
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
    def test_page_is_in_the_keys_volume(self):
        """a key never resolves into another volume (the page table once put SDM 1:8.5 on a Vol. 3A page): where the page has a printed footer it names the key's volume"""
        with open(os.path.join(os.path.dirname(HERE), 'sdm_pages.tsv'), encoding='utf-8') as fh: tab = [l.rstrip('\n').split('\t') for l in fh][1:]
        bad = [r for r in tab if r[2] and not r[2].startswith('Vol. %s ' % r[0].split()[1].split(':')[0])]
        self.assertEqual(bad, [])
def snap(o, nd, n=2):
    """(TOP, FSW, ST(0..n-1) patterns) of the spec result with the nondeterministic variables set: nd = [(var, value)]"""
    s = o.s
    for v, x in nd: s = X.St(**{f: z3.substitute(getattr(s, f), (v, x)) for f in X.St.FIELDS})
    top = val(X.top(s)); out = [rd(s, (top + i) & 7) for i in range(n)]
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
class SpecialTables(unittest.TestCase):
    """the special-value tables of the transcendental group, read from the SDM (rows in x87_sdm_rows.txt)"""
    VAL = {'-inf': float('-inf'), '-F': -2.0, '-0': -0.0, '+0': 0.0, '+F': 2.0, '+inf': float('inf'), 'NaN': NAN}
    COL = {'-inf': float('-inf'), '-F': -1.0, '-0': -0.0, '+0': 0.0, '+F': 1.0, '+inf': float('inf'), 'NaN': NAN}
    @staticmethod
    def cls(b):
        e, f, sg = (b >> 52) & 0x7ff, b & ((1 << 52) - 1), b >> 63
        if e == 0x7ff: return 'NaN' if f else ('-inf' if sg else '+inf')
        if e == 0 and f == 0: return '-0' if sg else '+0'
        return '-F' if sg else '+F'
    def test_fscale_table_1_30(self):
        cols = list(self.COL)
        for r in rows('fscale'):
            for c, want in zip(cols, r[1].split()):
                o = run(('d9', 'reg', 7, 5), state([self.VAL[r[0]], self.COL[c]]))
                s = o.s; top = val(X.top(s))
                self.assertEqual(self.cls(rd(s, top)), want, 'ST(0)=%s ST(1)=%s' % (r[0], c))
    def test_fpatan_table_1_26(self):
        """every concrete cell of the table (angles that are multiples of pi, signed zeros); a 'range' cell is symbolic in the spec (ATAN2) and is skipped"""
        V = {'-inf': float('-inf'), '-F': -1.0, '-0': -0.0, '+0': 0.0, '+F': 1.0, '+inf': float('inf')}
        cols = list(V); n = 0
        for r in rows('fpatan'):
            for c, want in zip(cols, r[1].split()):
                if want == 'range': continue
                o = run(('d9', 'reg', 6, 3), state([V[c], V[r[0]]]))                     # ST(0) = X, ST(1) = Y
                top = val(X.top(o.s)); got = struct.unpack('<d', struct.pack('<Q', rd(o.s, top)))[0]
                w = float(Fraction(want.lstrip('-+')) * Fraction(math.pi)) if want.lstrip('-+') not in ('0',) else 0.0
                w = -w if want.startswith('-') else w
                self.assertEqual(math.copysign(1, got), math.copysign(1, w), 'Y=%s X=%s' % (r[0], c))
                self.assertLess(abs(got - w), 1e-15, 'Y=%s X=%s' % (r[0], c)); n += 1
        self.assertEqual(n, 32)

    def test_fyl2x_table_1_44(self):
        """every cell of the table; the finite-by-finite cells ('+F', '-F' with a finite log) are symbolic (LOG2) and are checked by class only where the spec is concrete"""
        r1 = {'-inf': float('-inf'), '-F': -3.0, '-0': -0.0, '+0': 0.0, '+F': 3.0, '+inf': float('inf'), 'NaN': NAN}
        c0 = [('-inf', float('-inf')), ('-F', -2.0), ('+-0', -0.0), ('+-0', 0.0), ('<1', 0.5), ('1', 1.0), ('>1', 4.0), ('+inf', float('inf')), ('NaN', NAN)]
        # table columns: -inf, -F, +-0, 0<F<1, 1, F>1, +inf, NaN  (the +-0 column is run for both zeros)
        colmap = [0, 1, 2, 2, 3, 4, 5, 6, 7]
        n = 0
        for r in rows('fyl2x'):
            cells = r[1].split()
            for (cn, xv), ci in zip(c0, colmap):
                want = cells[ci]
                o = run(('d9', 'reg', 6, 1), state([xv, r1[r[0]]]))
                top = val(X.top(o.s)); raised = z3.simplify(o.raised)
                try: b = rd(o.s, top)
                except AssertionError: self.assertIn(want, ('+F', '-F')); continue          # symbolic finite result
                fl = val(raised)
                if want == '*': self.assertEqual((self.cls(b), fl & 1), ('NaN', 1), (r[0], cn))
                elif want == '**': self.assertEqual(fl & 4, 4, (r[0], cn)); self.assertEqual(self.cls(b), '+inf' if r1[r[0]] < 0 else '-inf', (r[0], cn))
                elif want == 'NaN': self.assertEqual(self.cls(b), 'NaN', (r[0], cn))
                else: self.assertEqual(self.cls(b), want, (r[0], cn)); self.assertEqual(fl, 0, (r[0], cn))
                n += 1
        self.assertEqual(n, 7 * 9 - 4)                                                     # 63 runs, 4 of them symbolic finite results (ST(1) finite, ST(0) < 1 or > 1)

class MmxAlias(unittest.TestCase):
    """MMX aliasing, SDM 1:9.5.1 / 1:9.6.2 / 1:9.4.8: one copy of the effect, on the DSL names"""
    def d(self):
        return {k: z3.BitVec("p." + k, w) for k, w in X.DSL_WIDTH.items()}
    def prove(self, f):
        sv = z3.Solver(); sv.add(z3.Not(f)); self.assertEqual(sv.check(), z3.unsat)
    def test_write(self):
        d = self.d(); v = z3.BitVec("v", 64)
        for n in (0, 3, 7):
            r = X.mmx_write_effect(d, n, v)
            self.prove(r["ftop"] == 0); self.prove(r["ftag"] == 0)
            self.prove(r["fr%d" % n] == z3.Concat(z3.BitVecVal(0xFFFF, 16), v))
            self.prove(X.mmx_read(r, n) == v)
            for m in range(8):
                if m != n: self.prove(r["fr%d" % m] == d["fr%d" % m])
            for k in ("fcw", "fsw", "fip", "fdp", "fop", "fcs", "fds"): self.prove(r[k] == d[k])
    def test_exec_other_registers_and_emms(self):
        d = self.d(); r = X.mmx_exec_effect(d)
        self.prove(r["ftop"] == 0); self.prove(r["ftag"] == 0)
        for m in range(8): self.prove(r["fr%d" % m] == d["fr%d" % m])
        e = X.emms_effect(d)
        self.prove(e["ftag"] == 0xFFFF); self.prove(e["ftop"] == d["ftop"])
        for m in range(8): self.prove(e["fr%d" % m] == d["fr%d" % m])


class TableCells(unittest.TestCase):
    """every cell of SDM 2A:FSIN Table 1-31, FPTAN 1-29, FSINCOS 1-32, F2XM1 1-12, FPREM 1-27 and FPREM1 1-28 (rows in x87_sdm_rows.txt)"""
    SRC = {'-inf': float('-inf'), '-F': -2.0, '-0': -0.0, '+0': 0.0, '+F': 2.0, '+inf': float('inf'), 'NaN': NAN}
    PUSH, C2, N = z3.Bool('nd.trig.push'), z3.Bool('nd.trig.c2'), z3.BitVec('nd.prem.N', 16)
    cls = staticmethod(SpecialTables.cls)
    @staticmethod
    def snapx(o, nd, n=2):
        """snap, but a register that is a symbolic value (a transcendental of a finite operand) is None, and fsw is reduced to its IE bit (None if symbolic)"""
        s = o.s
        for v, x in nd: s = X.St(**{f: z3.substitute(getattr(s, f), (v, x)) for f in X.St.FIELDS})
        def c(e):
            r = z3.simplify(e); return r.as_long() if z3.is_bv_value(r) else None
        top = c(X.top(s))
        return top, c(z3.ZeroExt(15, z3.Extract(0, 0, s.fsw))), [c(X.view64(z3.Select(s.R, K((top + i) & 7, 3)), K(0x037F, 16))) for i in range(n)]
    def check(self, cls_got, flags, want, who):
        if want == '*': self.assertEqual((cls_got, flags & 1), ('NaN', 1), who)
        elif want == 'NaN': self.assertEqual(cls_got, 'NaN', who)
        elif want == 'range': self.assertEqual(flags & 1, 0, who)
        else: self.assertEqual(cls_got, {'+1': '+F'}.get(want, want), who)
    def trig(self, name, key, ncols):
        n = 0
        for r in rows(name):
            o = run(key, state([self.SRC[r[0]]]))
            top, fsw, regs = self.snapx(o, [(self.PUSH, z3.BoolVal(False)), (self.C2, z3.BoolVal(False))])      # no push: ST(0) is the DEST column (sine / tangent)
            want = r[1].split()[-1]
            if want == 'range': self.assertEqual(fsw, 0, (name, r[0])); n += 1; continue
            res = regs[1] if (name == 'fptan' and top == 6) else regs[0]                                      # FPTAN pushes 1.0 above the tangent
            self.check(self.cls(res), fsw, want, (name, r[0])); n += 1
        self.assertEqual(n, 7)
    def test_fsin_table_1_31(self): self.trig('fsin', ('d9', 'reg', 7, 6), 1)
    def test_fptan_table_1_29(self): self.trig('fptan', ('d9', 'reg', 6, 2), 1)
    def test_fsincos_table_1_32(self):
        for r in rows('fsincos'):
            cosw, sinw = r[1].split()
            o = run(('d9', 'reg', 7, 3), state([self.SRC[r[0]]]))
            top, fsw, regs = self.snapx(o, [(self.PUSH, z3.BoolVal(True)), (self.C2, z3.BoolVal(False))])           # pushed: ST(0) = cosine, ST(1) = sine
            if cosw == 'range':
                self.assertEqual(fsw, 0, r[0]); continue
            self.assertEqual(top, 6, r[0])
            self.check(self.cls(regs[0]), fsw, cosw, ('cos', r[0])); self.check(self.cls(regs[1]), fsw, sinw, ('sin', r[0]))
            if cosw == '+1': self.assertEqual(regs[0], 0x3FF0000000000000, r[0])
    def test_f2xm1_table_1_12(self):
        key = ('d9', 'reg', 6, 0)
        for v, want in ((-0.0, -0.0), (0.0, 0.0)):
            o = run(key, state([v])); top, fsw, regs = snap(o, [])
            self.assertEqual((rd(o.s, top), fsw & 0x3f), (f2b(want), 0), v)
        for v in (-1.0, -0.5, 0.5, 1.0):                                                                      # in range: the result is a finite value of the listed range, no exception but #P
            o = run(key, state([v])); self.assertEqual(val(z3.Extract(3, 0, o.raised)), 0, v)        # IE, DE, ZE, OE raise nothing (UE and PE depend on the symbolic 2^x - 1)
        # out of range: the result is the admissible choice, any image (no precondition)
        o = run(key, state([3.0])); self.assertTrue(z3.is_true(z3.simplify(o.pre)))
        self.assertEqual([str(v) for v in o.nd], ['nd.f2xm1.out'])
        for want in (0, 0x3FFF8000000000000000):
            self.assertEqual(val(z3.Select(X.St(**{f: z3.substitute(getattr(o.s, f), (o.nd[0], K(want, 80))) for f in X.St.FIELDS}).R, K(7, 3))), want)
    def prem(self, name, key, nearest):
        V = {'-inf': float('-inf'), '-F': -5.0, '-0': -0.0, '+0': 0.0, '+F': 5.0, '+inf': float('inf'), 'NaN': NAN}
        C = {'-inf': float('-inf'), '-F': -3.0, '-0': -0.0, '+0': 0.0, '+F': 3.0, '+inf': float('inf'), 'NaN': NAN}
        n = 0
        for r in rows(name):
            for c, want in zip(list(C), r[1].split()):
                o = run(key, state([V[r[0]], C[c]]))                                                         # ST(0) = dividend, ST(1) = modulus
                top, fsw, regs = self.snapx(o, [(self.N, K(63, 16))])
                who = (name, r[0], c)
                if want == 'S': self.assertEqual(regs[0], f2b(V[r[0]]), who)
                elif '/' in want:
                    okc = [{'F': ('-F', '+F'), '-F': ('-F',), '+F': ('+F',)}.get(w, (w,)) for w in want.split('/')]
                    self.assertIn(self.cls(regs[0]), sum(okc, ()), who)
                else: self.check(self.cls(regs[0]), fsw, want, who)
                n += 1
        self.assertEqual(n, 49)
    def test_fprem_table_1_27(self): self.prem('fprem', ('d9', 'reg', 7, 0), False)
    def test_fprem1_table_1_28(self): self.prem('fprem1', ('d9', 'reg', 6, 5), True)

class StoresBcd(unittest.TestCase):
    """SDM 2A:FBSTP Table 1-15 and the FBLD/FILD/FIST/FST value paths, cell by cell (exact class on the image)"""
    FBSTP = ('df', 'mem', 6, None)
    def st(self, v, key, fcw=0x037F):
        o = run(key, state([v], fcw=fcw)); return val(o.mem), val(o.raised)
    def test_fbstp_table_1_15(self):
        bcd = lambda sign, n: (sign << 79) | int(str(n), 16)                                        # packed BCD digits of n
        ind = 0xFFFFC000000000000000
        cases = [(float('-inf'), ind, 1), (float('inf'), ind, 1), (NAN, ind, 1), (1e18, ind, 1), (-1e18, ind, 1),          # '*': -inf, +inf, NaN, too large
                 (-5.0, bcd(1, 5), 0), (5.0, bcd(0, 5), 0), (-0.0, bcd(1, 0), 0), (0.0, bcd(0, 0), 0),                      # F <= -1: -D; +-0 keep the sign
                 (-0.25, bcd(1, 0), 0x20), (0.25, bcd(0, 0), 0x20),                                                          # '**': +-0 or +-1 by rounding, with #P
                 (999999999999999999.0, ind, 1)]                                                                             # 10^18 - 1 is not a binary64 value: it rounds to 10^18, too large
        for v, want, fl in cases:
            got, raised = self.st(v, self.FBSTP)
            self.assertEqual(got, want, v); self.assertEqual(raised & 0x21, fl & 0x21, v)
        for rcv, want in ((0, bcd(1, 0)), (1, bcd(1, 1)), (2, bcd(1, 0)), (3, bcd(1, 0))):                                    # -0.25 under RC nearest, down, up, zero
            self.assertEqual(self.st(-0.25, self.FBSTP, 0x037F | rcv << 10)[0], want, rcv)
        for rcv, want in ((0, bcd(0, 0)), (1, bcd(0, 0)), (2, bcd(0, 1)), (3, bcd(0, 0))):                                    # +0.25
            self.assertEqual(self.st(0.25, self.FBSTP, 0x037F | rcv << 10)[0], want, rcv)
    def test_fbstp_unsupported_and_empty(self):
        o = run(self.FBSTP, state([('img', 0xFFFF0000000000000001)]))                                                         # an MMX write: unsupported
        self.assertEqual((val(o.mem), val(o.raised) & 1), (0xFFFFC000000000000000, 1))
    def test_fbld_roundtrip(self):
        for img_digits, want in ((0x123456789012345678, 123456789012345678), (0x80000000000000000045, -45)):
            o = X.execute(('df', 'mem', 4, None), state([]), K(img_digits, 80), None, None)
            r = val(z3.Select(o.s.R, K(7, 3)))
            sign, e, sig = r >> 79, (r >> 64) & 0x7FFF, r & (2**64 - 1)
            mag = Fraction(sig) * Fraction(2) ** (e - 16383 - 63)
            self.assertEqual((-1 if sign else 1) * mag, want)                                                                      # exact
            self.assertEqual(val(o.raised), 0)                                                                                  # FBLD raises nothing for decimal digits
    def test_fbld_invalid_digit_is_free(self):
        """SDM 2A:FBLD Description: a digit AH..FH gives an undefined result -> the image is the free nd.fbld.img, no precondition"""
        nd = z3.BitVec('nd.fbld.img', 80)
        for digits in (0x0000000000000000A0, 0x123456789012345F78, 0xFFFFC000000000000000 & 0xFFFFFFFFFFFFFFFFFFFF):
            o = X.execute(('df', 'mem', 4, None), state([]), K(digits, 80), None, None)
            self.assertIn(str(nd), [str(v) for v in o.nd])
            self.assertTrue(val(o.pre))                                                                                          # no precondition
            for img in (0, INDEF, 0x3FFF8000000000000000):
                r = val(z3.substitute(z3.Select(o.s.R, K(7, 3)), (nd, K(img, 80))))
                self.assertEqual(r, img, hex(digits))                                                                            # any image is admissible
    def test_fld_denormal_unmasked_still_pushes(self):
        """SDM 2A:FLD Description NOTE: a denormal load with DM unmasked flags the exception but the value is still pushed;
        SDM 1:8.5.1.1: an unmasked #IA (SNaN) leaves TOP and the registers unchanged"""
        for key, mem, want in ((('d9', 'mem', 0, None), 0x00000001, 2.0 ** -149), (('dd', 'mem', 0, None), 0x1, 5e-324)):
            for fcw, pushed in ((0x037D, True), (0x037F, True)):
                o = X.execute(key, state([], fcw), K(mem, 32 if key[0] == 'd9' else 64), None, None)
                s = o.s; top = val(X.top(s)); fsw = val(s.fsw)
                self.assertEqual(top, 7, (key, hex(fcw)))                                   # pushed in both cases
                self.assertEqual((fsw >> X.DE) & 1, 1, (key, hex(fcw)))                    # DE flagged
                self.assertEqual((fsw >> X.ES) & 1, int(fcw == 0x037D), (key, hex(fcw)))   # ES only when unmasked
                self.assertEqual(rd(s, 7), f2b(want), key)                                  # normalised, exact
        o = X.execute(('dd', 'mem', 0, None), state([], 0x037E), K(0x7FF0000000000001, 64), None, None)   # SNaN, IM unmasked
        self.assertEqual((val(X.top(o.s)) , val(o.s.fsw) & 1), (0, 1))                       # TOP unchanged (empty stack: TOP = 0), IE set
    def test_fiarith_forms(self):
        """SDM 2A:FIADD Op 'DEST := DEST + ConvertToDoubleExtendedPrecisionFP(SRC)' and the FISUB/FISUBR/FIMUL/FIDIV/FIDIVR rows"""
        ops = {0: lambda a, b: a + b, 1: lambda a, b: a * b, 4: lambda a, b: a - b, 5: lambda a, b: b - a, 6: lambda a, b: a / b, 7: lambda a, b: b / a}
        for op_, fn in ops.items():
            for opc, n, signed_max in (('da', 32, 2 ** 31 - 1), ('de', 16, 2 ** 15 - 1)):
                for a, m in ((2.5, 7), (2.5, -3), (-1.75, 1000), (0.5, signed_max), (0.5, -signed_max - 1)):
                    mem = m & (2 ** n - 1)
                    o = X.execute((opc, 'mem', op_, None), state([a]), K(mem, n), None, None)
                    got = rd(o.s, 7)
                    self.assertEqual(got, f2b(fn(a, float(m))), (opc, op_, a, m))
                    self.assertEqual(val(X.top(o.s)), 7)
        o = X.execute(('da', 'mem', 6, None), state([2.5]), K(0, 32), None, None)          # FIDIV by integer 0: #Z, SDM 2A:FDIV/FIDIV Exc
        self.assertEqual((val(o.s.fsw) >> X.ZE) & 1, 1)
        o = X.execute(('da', 'mem', 7, None), state([0.0]), K(5, 32), None, None)          # FIDIVR: 5 / ST(0) = 5 / 0
        self.assertEqual((val(o.s.fsw) >> X.ZE) & 1, 1)
        o = X.execute(('da', 'mem', 0, None), state([]), K(1, 32), None, None)             # ST(0) empty: stack underflow #IS
        self.assertEqual((val(o.s.fsw) >> X.SF) & 1, 1)
    def test_fist_ranges(self):
        for n, key in ((16, ('df', 'mem', 2, None)), (32, ('db', 'mem', 2, None))):
            hi, lo = 2 ** (n - 1) - 1, -(2 ** (n - 1))
            for v, want, ie in ((float(hi), hi, 0), (float(lo), lo, 0), (hi + 0.5, 1 << (n - 1), 1), (lo - 0.75, 1 << (n - 1), 1), (float(hi + 1), 1 << (n - 1), 1),
                                (-0.5, 0, 0), (0.5, 0, 0), (1.5, 2, 0), (2.5, 2, 0)):                                         # RNE: ties to even
                got, raised = self.st(v, key)
                self.assertEqual((got & ((1 << n) - 1), raised & 1), (want & ((1 << n) - 1), ie), (n, v))
    def test_fst_m32_m64_cells(self):
        f32 = lambda f: struct.unpack('<I', struct.pack('<f', f))[0]
        big80 = ('img', 0x7FFE8000000000000000)                                                                              # about 2^16383: overflows both
        for v, w32 in ((1.5, f32(1.5)), (-0.0, 0x80000000), (float('inf'), 0x7F800000)):
            self.assertEqual(self.st(v, ('d9', 'mem', 2, None))[0], w32, v)
        got, raised = self.st(big80, ('d9', 'mem', 2, None)); self.assertEqual((got, raised & 0x28), (0x7F800000, 0x28))      # OE and PE, infinity under RNE
        got, raised = self.st(big80, ('dd', 'mem', 2, None)); self.assertEqual((got, raised & 0x28), (0x7FF0000000000000, 0x28))
        for rcv, want in ((3, 0x7F7FFFFF), (1, 0x7F7FFFFF), (2, 0x7F800000)):                                                # positive overflow under RC zero, down, up
            self.assertEqual(self.st(big80, ('d9', 'mem', 2, None), 0x037F | rcv << 10)[0], want, rcv)
        got, raised = self.st(('img', 0x7FFFA000000000000000), ('dd', 'mem', 2, None))                                          # an SNaN image: IE and the quieted NaN
        self.assertEqual((got >> 51 & 0x1FFF, raised & 1), (0xFFF, 1))
        got, raised = self.st(('img', 0x00000000000000000001), ('dd', 'mem', 2, None))                                          # 80-bit denormal: zero with #U and #P
        self.assertEqual((got, raised & 0x30), (0, 0x30))

class NanRule(unittest.TestCase):
    """SDM 1:T4-8, x87 column, every row, through FADD ST(0), ST(1): (ST(0), ST(1)) -> result"""
    QA, QB = 0x7FF8000000000005, 0x7FF8000000000007         # QNaNs, B has the larger significand
    SA, SB = 0x7FF0000000000005, 0x7FF0000000000007         # SNaNs
    def add(self, a, b, tie=False):
        o = run(('d8', 'reg', 0, None), state([a, b]), i=1)
        o = X.St(**{f: z3.substitute(getattr(o.s, f), (X.NAN_TIE, z3.BoolVal(tie))) for f in X.St.FIELDS})
        return rd(o, val(X.top(o))), val(o.fsw) & 1
    def test_rows(self):
        q = 0x0008000000000000
        self.assertEqual(self.add(self.SA, self.QB), (self.QB, 1))              # SNaN and QNaN: the QNaN operand
        self.assertEqual(self.add(self.QA, self.SB), (self.QA, 1))
        self.assertEqual(self.add(self.SA, self.SB), (self.SB | q, 1))          # two SNaNs: the larger significand, converted
        self.assertEqual(self.add(self.SB, self.SA), (self.SB | q, 1))
        self.assertEqual(self.add(self.QA, self.QB), (self.QB, 0))              # two QNaNs: the larger significand
        self.assertEqual(self.add(self.QB, self.QA), (self.QB, 0))
        self.assertEqual(self.add(self.SA, 1.5), (self.SA | q, 1))              # SNaN and a value: the SNaN converted
        self.assertEqual(self.add(1.5, self.SA), (self.SA | q, 1))
        self.assertEqual(self.add(self.QA, 1.5), (self.QA, 0))                  # QNaN and a value
        self.assertEqual(self.add(1.5, self.QA), (self.QA, 0))
    def test_equal_significand_different_sign_is_the_choice(self):
        pos, neg = self.QA, self.QA | (1 << 63)
        for tie in (True, False):
            r, _ = self.add(pos, neg, tie); self.assertEqual(r, pos if tie else neg)        # ST(0) = pos, ST(1) = neg: either operand
        o = run(('d8', 'reg', 0, None), state([1.0, 2.0]), i=1)
        self.assertEqual([str(v) for v in o.nd], ['nd.nan.tie'])

class MmxThenX87(unittest.TestCase):
    """x87 after MMX without EMMS: an MMX write leaves fr<n> = 0xFFFF:v64 (SDM 1:9.6.2) with tags 00 and TOP = 0; that image has integer bit
    v64[63] and may be an unsupported encoding (exponent 7FFFH with integer bit 0).  The spec takes it as a state, no precondition."""
    def mmx_state(self, v64, n=0):
        d = X.mmx_write_effect({"fr%d" % i: K(0, 80) for i in range(8)} | {"ftop": K(5, 3), "ftag": K(0xFFFF, 16), "fcw": K(0x037F, 16), "fsw": K(0, 16),
                                "fip": K(0, 32), "fdp": K(0, 32), "fop": K(0, 11), "fcs": K(0, 16), "fds": K(0, 16)}, n, K(v64, 64))
        return X.from_dsl(d)
    def test_fadd_of_mmx_data_with_integer_bit_clear_is_invalid(self):
        s = self.mmx_state(0x0000000000000001)                                    # fr0 = FFFF:0000000000000001: exponent 7FFF, integer bit 0, unsupported
        self.assertEqual(val(X.tag_at(s, K(0, 3))), 0)                           # the MMX write left the tag valid (SDM 1:9.5.1) ...
        o = run(('d8', 'reg', 0, None), s.copy(tag=K(0, 16)), i=0)                # FADD ST(0), ST(0)
        self.assertEqual(val(o.s.fsw) & 1, 1)                                     # ... and the operation is an invalid operation (SDM 2A:FADD Exc '#IA')
        self.assertEqual(val(z3.Select(o.s.R, K(0, 3))), X.INDEF80)               # masked response: the QNaN indefinite
        self.assertEqual((val(o.s.fsw) >> X.SF) & 1, 0)                           # not a stack fault
    def test_fadd_of_mmx_data_with_integer_bit_set_is_a_number(self):
        s = self.mmx_state(0x8000000000000000)                                    # FFFF:8000000000000000: a QNaN-class image (exponent 7FFF, integer 1, fraction 0 = infinity)
        o = run(('d8', 'reg', 0, None), s, i=0)
        self.assertEqual(val(o.s.fsw) & 1, 0)                                     # +inf + +inf... sign bit is the exponent's bit 15 = 1: -inf + -inf = -inf, no exception
        self.assertEqual(val(z3.Select(o.s.R, K(0, 3))), 0xFFFF8000000000000000)
    def test_fstp_m80_copies_the_image(self):
        for v in (0x0000000000000001, 0x8000000000000000, 0xC000000000000000):
            s = self.mmx_state(v)
            o = run(('db', 'mem', 7, None), s)                                    # FSTP m80fp: no exception for any encoding (SDM 2A:FST Exc)
            self.assertEqual(val(o.mem), 0xFFFF0000000000000000 | v, hex(v))
            self.assertEqual(val(o.s.fsw) & 0x3F, 0)

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
        self.assertEqual(val(d['fsw'] & K(7 << 11, 16)), 0)                                      # fsw carries no TOP
        self.assertEqual(val(d['ftop']), 5)
    def test_every_image_is_a_state(self):
        # no precondition on the registers: a 80-bit pattern that is not a binary64 value survives the round trip bit for bit
        d = X.to_dsl(state([1.0])); d['fr7'] = K(0x3FFF8000000000000001, 80)                       # 1 + 2^-63 (register 7 is ST(0))
        self.assertEqual(val(X.to_dsl(X.from_dsl(d))['fr7']), 0x3FFF8000000000000001)
    def test_fault_kinds(self):
        self.assertEqual(tuple(X.FLT[n] for n in ('FLT_IE', 'FLT_DE', 'FLT_ZE', 'FLT_OE', 'FLT_UE', 'FLT_PE', 'FLT_SF')), (12, 13, 14, 15, 16, 17, 18))   # read from fnvlift/faults.json
        fdiv_st0 = ('d8', 'reg', 6, None)                                                            # FDIV ST(0), ST(i)
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([1.0, 0.0], fcw=0x037B), i=1))), X.FLT["FLT_ZE"])    # 1/0, ZE unmasked (SDM 1:8.5.3)
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([1.0, 0.0], fcw=0x037F), i=1))), 0)           # masked
        self.assertEqual(val(X.fault_kind(run(('d8', 'reg', 0, None), state([], fcw=0x037E), i=1))), X.FLT["FLT_SF"])   # FADD on an empty stack: IE with SF
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([0.0, 0.0], fcw=0x037A), i=1))), X.FLT["FLT_IE"])       # 0/0: IE wins over ZE (Wine order), a pure IE
if __name__ == '__main__':
    unittest.main()
