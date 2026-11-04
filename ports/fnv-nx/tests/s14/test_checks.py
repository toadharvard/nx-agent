#!/usr/bin/env python3
"""Tests for the self-consistency checks of the synthetic data image (loader/synth/synth_checks.py, stream S14), on
hand-made MSVC-style COFF objects (no decomp content). Each check gets planted violations and clean cases:

  pointers   a table with a pointer to a mapped function, to a function the address map lacks, to an unknown
             .text address, to a name nobody defines and to a literal; a vftable slot inside a function
  vftables   slots whose definition is the slot's own symbol, a cdecl function (convention), a member taking one
             parameter fewer (arity), a cdecl function without parameters (ignores `this`), not a function start
  rtti       a complete COL -> TD -> CHD -> BCA -> BCD chain; a COL naming another class's TypeDescriptor; the same
             CHD with another hierarchy and the same COL spelled struct in another object; TDs at one address
             spelled class/struct (majority); a TD whose vptr names type_info's vftable that no object defines;
             a literal ending on a vftable's [-1] word (no COL there in the original)
  overlaps   two __real@ constants at one address (contested: 0 in the shell), a .bss int over a literal
  strings    $SG literals of 4 characters (terminator after the last word: the J-0062 bug), of 2 characters
             and UTF-16

    python3 -I tests/s14/test_checks.py [-v]

Set FNVNX_S14_RUN to a synth_image.py output directory (with checks.json) to also check a real run: no
violation owned by S14.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'loader', 'synth'))
import synth_checks  # noqa: E402
import synth_image  # noqa: E402
from test_synth import coff, COMDAT, RD, RW  # noqa: E402

DIR32 = 6
FA, FB, FC, FD, FU, TX = 0x00500000, 0x00500100, 0x00500200, 0x00500300, 0x00500400, 0x00600000
VT_BAR, VT_QUX = 0x01020004, 0x01020104
TD_BAR, TI_VT, MISSING = 0x01183100, 0x01030000, 0x01030100
LIT = '??_C@_03ABCDEFGH@xyz?$AA@'
LIT2 = '??_C@_04ABCDEFGH@abcd?$AA@'      # ends on Qux's [-1] word (a class without a COL in the original)
LIT_VA, SG_VA, REAL_VA, TAB_VA = 0x01010000, 0x01011000, 0x01012000, 0x01183200
DR = 0xC0000040          # .data$r (TypeDescriptors are writable)


def rtti_sections(key, n_bases, td_key=None):
    """COL, TD, CHD, BCA and BCDs of class Bar (n_bases BCDs; the first is Bar itself)"""
    td = '??_R0?A%sBar@@@8' % (td_key or key)
    secs = [
        ['.rdata$r', RD | COMDAT, struct.pack('<5I', 0, 0, 0, 0, 0), [(12, td, DIR32), (16, '??_R3Bar@@8', DIR32)], 2],
        ['.data$r', DR | COMDAT, struct.pack('<II', 0, 0) + ('.?A%sBar@@' % (td_key or key)).encode() + b'\0\0\0',
         [(0, '??_7type_info@@6B@', DIR32)], 2],
        ['.rdata$r', RD | COMDAT, struct.pack('<4I', 0, 0, n_bases, 0), [(12, '??_R2Bar@@8', DIR32)], 2],
        ['.rdata$r', RD | COMDAT, bytes(4 * n_bases + 4), [(4 * k, '??_R1BCD%d@Bar@@8' % k, DIR32)
                                                            for k in range(n_bases)], 2],
    ]
    for k in range(n_bases):
        secs.append(['.rdata$r', RD | COMDAT, struct.pack('<7I', 0, n_bases - 1 if k == 0 else 0, 0, 0xffffffff, 0,
                                                          0x40, 0),
                     [(0, td, DIR32), (24, '??_R3Bar@@8', DIR32)], 2])
    names = ['??_R4Bar@@6B@', td, '??_R3Bar@@8', '??_R2Bar@@8'] + ['??_R1BCD%d@Bar@@8' % k for k in range(n_bases)]
    return secs, names, td


def unit_a():
    slots = ['?a@Bar@@UAEXH@Z', '?b@Bar@@UAEXH@Z', '?c@Bar@@UAEXHH@Z', '?d@Bar@@UAEXXZ', '?e@Bar@@UAEXXZ']
    rs, rnames, td = rtti_sections('V', 1)
    sections = [
        ['.rdata', RD | COMDAT, bytes(4 + 4 * len(slots)),
         [(0, '??_R4Bar@@6B@', DIR32)] + [(4 + 4 * k, s, DIR32) for k, s in enumerate(slots)], 2],      # 1
        ['.rdata', RD | COMDAT, bytes(8), [(0, '??_R4Qux@@6B@', DIR32), (4, '?a@Bar@@UAEXH@Z', DIR32)], 2],   # 2
        ['.rdata$r', RD | COMDAT, bytes(20), [(12, '??_R0?AVOther@@@8', DIR32), (16, '??_R3Qux@@8', DIR32)], 2],  # 3
        ['.rdata', RD | COMDAT, b'xyz\0', [], 2],                                                          # 4
        ['.data', RW, bytes(20), [(0, '?a@Bar@@UAEXH@Z', DIR32), (4, '?FUN_00500400@@YAXXZ', DIR32),
                                  (8, '?FUN_00600000@@YAXXZ', DIR32), (12, '?g_missing@@3HA', DIR32),
                                  (16, LIT, DIR32)]],                                                     # 5
        ['.rdata', RD, b'abcd\0\0\0\0ef\0\0h\0i\0\0\0\0\0', []],                                         # 6
        ['.rdata', RD | COMDAT, struct.pack('<d', 1.0), [], 2],                                            # 7
        ['.bss', 0xC0000080, 16, []],                                                                      # 8
        ['.rdata', RD | COMDAT, b'abcd\0', [], 2],                                                         # 9
    ] + rs                                                                                                 # 10..
    base = len(sections) - len(rs) + 1
    symbols = [['??_7Bar@@6B@', 1, 4, 2, 0], ['??_7Qux@@6B@', 2, 4, 2, 0], ['??_R4Qux@@6B@', 3, 0, 2, 0],
               [LIT, 4, 0, 2, 0], ['?g_tab@@3PAPAXA', 5, 0, 2, 0],
               ['$SG100', 6, 0, 3, 0], ['$SG101', 6, 8, 3, 0], ['$SG102', 6, 12, 3, 0],
               ['__real@3ff0000000000000', 7, 0, 2, 0], ['?g_bss@@3HA', 8, 0, 2, 0], [LIT2, 9, 0, 2, 0]]
    symbols += [[n, base + k, 0, 2, 0] for k, n in enumerate(rnames)]
    for n in slots + ['?FUN_00500400@@YAXXZ', '?FUN_00600000@@YAXXZ', '?g_missing@@3HA', '??_7type_info@@6B@',
                      '??_R0?AVOther@@@8', '??_R3Qux@@8']:
        symbols.append([n, 0, 0, 2, 0])
    return coff(sections, symbols)


def unit_b(key_chd_bases=2):
    """another view of Bar: struct key, a second base; and another __real@ at the address of unit A's"""
    rs, rnames, td = rtti_sections('V', key_chd_bases, td_key='U')
    sections = [['.rdata', RD | COMDAT, struct.pack('<d', 2.0), [], 2]] + rs
    symbols = [['__real@4000000000000000', 1, 0, 2, 0]] + [[n, 2 + k, 0, 2, 0] for k, n in enumerate(rnames)]
    symbols.append(['??_7type_info@@6B@', 0, 0, 2, 0])
    return coff(sections, symbols)


def unit_c():
    """a third .?AU view of Bar, and a .bss int at the literal's address"""
    rs, rnames, td = rtti_sections('U', 1)
    rs.append(['.bss', 0xC0000080, 4, []])
    return coff(rs, [[n, 1 + k, 0, 2, 0] for k, n in enumerate(rnames)] +
                [['??_7type_info@@6B@', 0, 0, 2, 0], ['?g_zero@@3HA', len(rs), 0, 2, 0]])


def F(a, s, n):
    return {'address': '0x%08x' % a, 'symbol': s, 'size': n}


UNITS = {'units': [
    {'source': 'src/a/bar.cpp', 'compiler': 'sp1', 'flags': [],
     'functions': [F(FA, '?a@Bar@@UAEXH@Z', 0x20), F(FB, '?FUN_00500100@@YAXH@Z', 0x10),
                   F(FC, '?FUN_00500200@Baz@@QAEXH@Z', 0x10), F(FD, '?FUN_00500300@@YAXXZ', 0x10),
                   F(FU, '?FUN_00500400@@YAXXZ', 0x10)],
     'symbols': {'??_7Bar@@6B@': '0x%08x' % VT_BAR, '??_7Qux@@6B@': '0x%08x' % VT_QUX,
                 '?b@Bar@@UAEXH@Z': '0x%08x' % FB, '?c@Bar@@UAEXHH@Z': '0x%08x' % FC, '?d@Bar@@UAEXXZ': '0x%08x' % FD,
                 '?e@Bar@@UAEXXZ': '0x%08x' % (FA + 4), '?FUN_00600000@@YAXXZ': '0x%08x' % TX,
                 '?g_missing@@3HA': '0x%08x' % MISSING, '??_7type_info@@6B@': '0x%08x' % TI_VT,
                 LIT: '0x%08x' % LIT_VA, '?g_tab@@3PAPAXA': '0x%08x' % TAB_VA,
                 '$SG100': '0x%08x' % SG_VA, '$SG101': '0x%08x' % (SG_VA + 8), '$SG102': '0x%08x' % (SG_VA + 12),
                 '__real@3ff0000000000000': '0x%08x' % REAL_VA, '??_R0?AVBar@@@8': '0x%08x' % TD_BAR,
                 '?g_bss@@3HA': '0x011c4000', LIT2: '0x%08x' % (VT_QUX - 8)}},
    {'source': 'src/b/bar2.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [],
     'symbols': {'__real@4000000000000000': '0x%08x' % REAL_VA, '??_R0?AUBar@@@8': '0x%08x' % TD_BAR,
                 '??_7type_info@@6B@': '0x%08x' % TI_VT}},
    {'source': 'src/c/bar3.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [],
     'symbols': {'??_R0?AUBar@@@8': '0x%08x' % TD_BAR, '??_7type_info@@6B@': '0x%08x' % TI_VT,
                 '?g_zero@@3HA': '0x%08x' % LIT_VA}},
]}


class ChecksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = t = tempfile.mkdtemp(prefix='s14c-')
        os.makedirs(os.path.join(t, 'obj'))
        for i, data in enumerate((unit_a(), unit_b(), unit_c())):
            with open(os.path.join(t, 'obj', 'u%d.obj' % i), 'wb') as f:
                f.write(data)
        with open(os.path.join(t, 'units.json'), 'w') as f:
            json.dump(UNITS, f)
        with open(os.path.join(t, 'imports.tsv'), 'w') as f:
            f.write('# none\n')
        with open(os.path.join(t, 'addrmap.tsv'), 'w') as f:
            f.write('# fnvnx-addrmap 1\n# address\tkind\n')
            for a in (FA, FB, FC, FD):
                f.write('%08x\tF\t__fnv_%08x\n' % (a, a))
        p = lambda n: os.path.join(t, n)  # noqa: E731
        rc = synth_image.main(['--units', p('units.json'), '--objdir', p('obj'), '--imports', p('imports.tsv'),
                               '--addrmap', p('addrmap.tsv'), '--checks', p('checks.json'),
                               '--violations', p('violations.tsv'), '--state-out', p('state.bin'),
                               '--report', p('r.json'), '--out', p('s.exe')])
        assert rc == 0
        with open(p('checks.json')) as f:
            cls.c = json.load(f)
        with open(p('r.json')) as f:
            cls.r = json.load(f)
        with open(p('state.bin'), 'rb') as f:
            cls.state = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def n(self, check, kind):
        return self.c[check]['counts'].get(kind, 0)

    def test_pointers(self):
        self.assertEqual(self.n('pointers', 'ptr:code-function'), 1 + 4 + 1)     # g_tab[0]; Bar a..d; Qux 0
        self.assertEqual(self.n('pointers', 'ptr:code-not-linked'), 1)            # FU: units.json only
        self.assertEqual(self.n('pointers', 'ptr:code-unmapped'), 1)              # TX
        self.assertEqual(self.n('pointers', 'ptr:code-inside-function'), 1)       # slot e -> FA+4
        self.assertEqual(self.n('pointers', 'ptr:data-known'), 1)                 # the literal xyz
        self.assertEqual(self.n('pointers', 'ptr:data-undefined'), 2)             # g_missing, type_info vftable
        self.assertGreaterEqual(self.n('pointers', 'ptr:skip-unresolved'), 1)     # COL words

    def test_vftables(self):
        if not shutil.which(synth_checks.UNDNAME):
            self.skipTest('llvm-undname not installed')
        v = self.c['vftables']['counts']
        self.assertEqual(v.get('slot:same-name'), 2)                 # Bar::a, Qux slot 0
        self.assertEqual(v.get('slot:convention'), 1)                # b -> cdecl FUN_00500100(int)
        self.assertEqual(v.get('slot:arity'), 1)                     # c(int, int) -> Baz::FUN_00500200(int)
        self.assertEqual(v.get('slot:ignores-this'), 1)              # d() -> cdecl FUN_00500300()
        self.assertEqual(v.get('slot:not-function'), 1)              # e -> inside a
        self.assertNotIn('slot:fits', v)

    def test_fit_rules(self):
        import sigs
        dem = synth_checks.demangle(['?a@X@@UAEXH@Z', '?f@@YGXPAXH@Z', '?f@@YIXPAXHH@Z', '?f@X@@QAEXZZ'])
        if not dem:
            self.skipTest('llvm-undname not installed')
        p = lambda n: sigs.parse_msvc(n, dem.get(n))  # noqa: E731
        slot = p('?a@X@@UAEXH@Z')
        self.assertEqual(synth_checks.fit(slot, p('?f@@YGXPAXH@Z')), 'slot:convention')     # stdcall(this, int)
        self.assertEqual(synth_checks.fit(slot, p('?f@@YIXPAXHH@Z')), 'slot:fastcall-member-form')
        self.assertEqual(synth_checks.fit(slot, p('?f@X@@QAEXZZ')), 'slot:fits')            # varargs

    def test_rtti(self):
        c = self.c['rtti']['counts']
        self.assertEqual(c.get('rtti:col-ok'), 1)                    # Bar's chain in unit A
        self.assertEqual(c.get('rtti:col-td-class'), 1)              # Qux's COL names Other
        self.assertEqual(c.get('rtti:chd-disagree-units'), 1)        # unit B: two bases
        self.assertEqual(c.get('rtti:col-struct-key'), 1)            # .?AV vs .?AU, same offsets
        self.assertEqual(c.get('rtti:td-image-spelling'), 1)         # unit A's TD lost to two .?AU units
        self.assertEqual(c.get('rtti:col-unplaced'), 1)                # Bar
        self.assertEqual(c.get('rtti:col-word-other-item'), 1)         # Qux: the literal's NUL
        self.assertEqual(c.get('rtti:td-ok'), 1)                     # the .?AU TD the image holds

    def test_overlaps(self):
        o = self.c['overlaps']
        self.assertEqual(o['counts'].get('overlap:real-different-value'), 1)
        self.assertEqual(o['counts'].get('overlap:td-class-struct-key'), 1)  # B and C: one item, 2 units
        self.assertEqual(o['outcome'].get('real-different-value/contested'), 1)
        self.assertEqual(o['counts'].get('overlap:bss-vs-initialised'), 1)      # g_zero over "xyz"
        self.assertEqual(o['outcome'].get('bss-vs-initialised/initialised-kept'), 1)
        # contested bytes (1.0 vs 2.0: the top two) are unknown and the shell holds 0 there, not a claimant's byte
        sys.path.insert(0, os.path.join(ROOT, 'loader', 'tools'))
        import fnvnx_pe
        with open(os.path.join(self.tmp, 's.exe'), 'rb') as f:
            img = fnvnx_pe.Image(fnvnx_pe.PE(f.read()))
        self.assertEqual(img.read(REAL_VA, 8), bytes(8))
        self.assertEqual([self.state[REAL_VA - synth_image.WIN_LO + k] for k in range(8)], [3] * 6 + [2, 2])
        self.assertEqual(o['outcome'].get('td-class-struct-key/majority'), 1)

    def test_strings(self):
        s = self.c['strings']['counts']
        self.assertEqual(s.get('str:sg-terminated'), 3)
        self.assertNotIn('str:sg-nul-outside', s)
        self.assertEqual(s.get('str:pool-terminated'), 2)
        st = lambda va: self.state[va - synth_image.WIN_LO]  # noqa: E731
        self.assertEqual(st(SG_VA + 4), 3)          # "abcd" terminator owned (was soft before J-0062)
        self.assertEqual(st(SG_VA + 5), 1)          # padding after it: soft
        self.assertEqual(st(SG_VA + 12 + 5), 3)     # UTF-16 terminator pair
        self.assertEqual(synth_image.string_extent(b'abcd\0\0\0\0', 3), 5)
        self.assertEqual(synth_image.string_extent(b'h\0i\0\0\0\0\0', 2), 6)
        self.assertEqual(synth_image.string_extent(b'\0\0\0\0', -1), 1)
        self.assertEqual(synth_checks.terminator(b'h\0i\0\0\0', 0), 6)
        self.assertEqual(synth_checks.terminator(b'ab\0\0', 0), 3)

    def test_violations_by_kind_and_owner(self):
        v = self.c['violations_by_kind']
        self.assertEqual(v['ptr:code-not-linked']['owner'], 'S1 (unit not in the link)')
        for k, x in v.items():
            self.assertTrue(synth_checks.KINDS[k][0], k)
        with open(os.path.join(self.tmp, 'violations.tsv')) as f:
            rows = [x.split('\t') for x in f if not x.startswith('#')]
        self.assertEqual(len(rows), sum(x['count'] for x in v.values()))
        self.assertEqual(self.r['checks']['violations_by_kind'].keys(), v.keys())

    def test_coverage_report(self):
        b = self.r['coverage_bytes']
        self.assertIn('vftables', b['kinds'])
        self.assertIn('strings-sg', b['kinds'])
        self.assertEqual(b['kinds']['strings-sg']['known'], 5 + 3 + 6)
        self.assertEqual(b['kinds']['reals']['merged-disagree'], 0)       # contested: unknown
        self.assertGreater(b['kinds']['reals']['unknown'], 0)
        a = self.r['coverage_addresses']['kinds']
        self.assertEqual(a['strings-sg']['defined:one-object'], 3)
        self.assertEqual(a['rtti']['defined'], 1)                         # the TD address
        self.assertEqual(b['kinds']['rtti']['merged-disagree'], 1)        # its 'V'/'U' byte
        g = {x['label']: x for x in self.r['boot_globals']}
        self.assertIn('run-once flag of FUN_00aa3050', g)
        self.assertEqual(g['run-once flag of FUN_00aa3050']['state'], '-')

    def test_kinds_table(self):
        for k, (viol, owner, meaning) in synth_checks.KINDS.items():
            self.assertTrue(meaning)
            self.assertEqual(viol, owner is not None, k)


class RealRunTest(unittest.TestCase):
    """the checks on a real synth_image.py run (FNVNX_S14_RUN=<output dir with checks.json>)"""

    def test_no_s14_violations(self):
        d = os.environ.get('FNVNX_S14_RUN')
        if not d:
            self.skipTest('set FNVNX_S14_RUN to a synth_image.py output directory')
        with open(os.path.join(d, 'checks.json')) as f:
            c = json.load(f)
        mine = {k: v['count'] for k, v in c['violations_by_kind'].items() if v['owner'] == synth_checks.S14}
        self.assertEqual(mine, {})
        self.assertGreater(c['strings']['counts'].get('str:sg-terminated', 0), 1000)


if __name__ == '__main__':
    unittest.main()
