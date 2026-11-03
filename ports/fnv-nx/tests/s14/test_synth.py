#!/usr/bin/env python3
"""Tests for loader/synth (stream S14) on hand-made MSVC-style COFF objects (no decomp content).

    python3 -I tests/s14/test_synth.py [-v]

Two fake units: the first defines a string literal (COMDAT), an initialised int and a pointer to the literal
(non-COMDAT .data), a constructed object (.bss), a vftable with an RTTI locator nobody places and a slot to a
function of its own, and an initializer that constructs the object, stores the int, passes the literal and reads a
float the decomp only declares. The second repeats the literal (an agreeing copy), claims part of it with a
$SG of other content (loses to the content-named literal) and defines a __real@ constant. Checked: placement,
relocations, ranks, classes, the first consumer of the missing float, section inference, the PE32 shell (Python
model of S3's loader and, when built, the C loader through loadcheck) and the import directory.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'loader', 'synth'))
sys.path.insert(0, os.path.join(ROOT, 'loader', 'tools'))
import coffobj  # noqa: E402
import msvcname  # noqa: E402
import synth_image  # noqa: E402
import fnvnx_pe  # noqa: E402

COMDAT, RD, RW, CODE, BSS = 0x1000, 0x40000040, 0xC0000040, 0x60000020, 0xC0000080
ALIGN4, ALIGN16 = 0x00300000, 0x00500000


def coff(sections, symbols):
    """sections: [name, flags, data or size(int, bss), relocs [(off, symbol name, type)], comdat selection];
    symbols: [name, section (1-based, 0 undefined), value, storage, is_function]. Section symbols are added."""
    syms, idx = [], {}
    for i, s in enumerate(sections):
        syms.append((s[0], i + 1, 0, 3, 0, s))           # section symbol + aux
    for name, sec, val, storage, fn in symbols:
        syms.append((name, sec, val, storage, 0x20 if fn else 0, None))
    strtab = bytearray(4)
    table = bytearray()
    k = 0
    for name, sec, val, storage, typ, aux in syms:
        if name not in idx or aux is None:
            idx.setdefault(name, k)
        nb = name.encode()
        if len(nb) <= 8:
            raw = nb.ljust(8, b'\0')
        else:
            raw = struct.pack('<II', 0, len(strtab))
            strtab += nb + b'\0'
        table += raw + struct.pack('<IhHBB', val, sec, typ, storage, 1 if aux else 0)
        k += 1
        if aux:
            data = aux[2]
            size = data if isinstance(data, int) else len(data)
            table += struct.pack('<IHHIHB3x', size, len(aux[3]), 0, 0, 0, aux[4] if len(aux) > 4 else 0)
            k += 1
    struct.pack_into('<I', strtab, 0, len(strtab))
    head = 20 + 40 * len(sections)
    body = bytearray()
    shdrs = bytearray()
    for name, flags, data, relocs, *rest in sections:
        size = data if isinstance(data, int) else len(data)
        raw_off = 0 if isinstance(data, int) else head + len(body)
        if not isinstance(data, int):
            body += data
        rel_off = head + len(body) if relocs else 0
        for off, sname, typ in relocs:
            body += struct.pack('<IIH', off, idx[sname], typ)
        shdrs += struct.pack('<8sIIIIIIHHI', name.encode(), 0, 0, size, raw_off, rel_off, 0, len(relocs), 0, flags)
    symptr = head + len(body)
    hdr = struct.pack('<HHIIIHH', 0x14c, len(sections), 0, symptr, len(table) // 18, 0, 0)
    return hdr + shdrs + body + table + strtab


LIT = '??_C@_05ABCDEFGH@Hello?$AA@'
INIT, FN_F, CTOR = 0x00f35000, 0x00500000, 0x00500100


def unit0():
    code = bytearray(b'\xb9\0\0\0\0' b'\xe8\0\0\0\0' b'\xc7\x05\0\0\0\0\x05\0\0\0' b'\x68\0\0\0\0'
                     b'\xd9\x05\0\0\0\0' b'\xc3')
    sections = [
        ['.rdata', RD | COMDAT | ALIGN4, b'Hello\0', [], 2],                                   # 1 literal
        ['.data', RW | ALIGN4, struct.pack('<III', 0x11223344, 0, 7), [(4, LIT, 6)]],           # 2
        ['.bss', BSS | ALIGN4, 16, []],                                                        # 3
        ['.rdata', RD | COMDAT | ALIGN4, bytes(8), [(0, '??_R4Foo@@6B@', 6), (4, '?f@Foo@@UAEXXZ', 6)], 6],  # 4
        ['.text', CODE | COMDAT | ALIGN16, bytes(code),
         [(1, '?g_obj@@3UFoo@@A', 6), (6, '??0Foo@@QAE@XZ', 0x14), (12, '?g_a@@3HA', 6), (21, LIT, 6),
          (27, '?DAT_01012100@@3MA', 6)], 1],                                                  # 5
        ['.text', CODE | COMDAT | ALIGN16, b'\xc3', [], 1],                                    # 6
    ]
    symbols = [
        [LIT, 1, 0, 2, 0],
        ['?g_a@@3HA', 2, 0, 2, 0], ['?g_p@@3PBDB', 2, 4, 2, 0], ['?g_b@@3HA', 2, 8, 2, 0],
        ['?g_obj@@3UFoo@@A', 3, 0, 2, 0],
        ['??_7Foo@@6B@', 4, 4, 2, 0], ['??_R4Foo@@6B@', 0, 0, 2, 0],
        ['?init@@YAXXZ', 5, 0, 2, 1], ['??0Foo@@QAE@XZ', 0, 0, 2, 1], ['?DAT_01012100@@3MA', 0, 0, 2, 0],
        ['?f@Foo@@UAEXXZ', 6, 0, 2, 1],
    ]
    return coff(sections, symbols)


def unit1():
    sections = [
        ['.rdata', RD | COMDAT | ALIGN4, b'Hello\0', [], 2],
        ['.rdata', RD | ALIGN4, b'XY\0\0', []],
        ['.rdata', RD | COMDAT | ALIGN4, struct.pack('<f', 1.0), [], 2],
        ['.tls$', RW | ALIGN4, struct.pack('<I', 9), []],
    ]
    symbols = [[LIT, 1, 0, 2, 0], ['$SG5', 2, 0, 3, 0], ['__real@3f800000', 3, 0, 2, 0],
               ['?t_v@@3HA', 4, 0, 2, 0]]
    return coff(sections, symbols)


UNITS = {'units': [
    {'source': 'src/a/one.cpp', 'compiler': 'sp1', 'flags': [],
     'functions': [{'address': '0x%08x' % INIT, 'symbol': '?init@@YAXXZ', 'size': 32},
                   {'address': '0x%08x' % FN_F, 'symbol': '?f@Foo@@UAEXXZ', 'size': 1}],
     'symbols': {LIT: '0x01011000', '?g_a@@3HA': '0x01183010', '?g_p@@3PBDB': '0x01183014',
                 '?g_obj@@3UFoo@@A': '0x011c4000', '??_7Foo@@6B@': '0x01012004', '??0Foo@@QAE@XZ': '0x%08x' % CTOR,
                 '?DAT_01012100@@3MA': '0x01012100', '__imp__GetTickCount@0': '0x00fdf000'}},
    {'source': 'src/b/two.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [],
     'symbols': {LIT: '0x01011000', '$SG5': '0x01011002', '__real@3f800000': '0x01012200',
                 '?t_v@@3HA': '0x01272004'}},
]}


class SynthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='s14-')
        t = cls.tmp
        os.makedirs(os.path.join(t, 'obj'))
        with open(os.path.join(t, 'obj', 'u0.obj'), 'wb') as f:
            f.write(unit0())
        with open(os.path.join(t, 'obj', 'u1.obj'), 'wb') as f:
            f.write(unit1())
        with open(os.path.join(t, 'units.json'), 'w') as f:
            json.dump(UNITS, f)
        with open(os.path.join(t, 'imports.tsv'), 'w') as f:
            f.write('# iat_slot\tdll\tapi\n0x00fdf000\tkernel32\tGetTickCount\t1\t-\tS4\n'
                    '0x010b7eb0\t(not an IAT slot)\tFree\t1\t-\tnone\n')
        out = lambda n: os.path.join(t, n)  # noqa: E731
        rc = synth_image.main(['--units', out('units.json'), '--objdir', out('obj'), '--imports', out('imports.tsv'),
                               '--out', out('s.exe'), '--report', out('r.json'), '--missing', out('m.tsv'),
                               '--state-out', out('state.bin'), '--conflicts', out('c.tsv')])
        assert rc == 0
        with open(out('r.json')) as f:
            cls.r = json.load(f)
        with open(out('s.exe'), 'rb') as f:
            cls.exe = f.read()
        cls.pe = fnvnx_pe.PE(cls.exe)
        cls.img = fnvnx_pe.Image(cls.pe)
        with open(out('state.bin'), 'rb') as f:
            cls.state = f.read()

    def st(self, va):
        return self.state[va - synth_image.WIN_LO]

    def test_coff_reader(self):
        c = coffobj.Coff(unit0())
        self.assertEqual([s.name for s in c.sections[1:]], ['.rdata', '.data', '.bss', '.rdata', '.text', '.text'])
        self.assertTrue(c.sections[1].is_comdat and c.sections[1].comdat_sel == 2)
        self.assertTrue(c.sections[3].is_bss and c.sections[3].is_data)
        self.assertEqual(len(c.sections[5].relocs), 5)

    def test_names(self):
        self.assertEqual(msvcname.type_size('?g_a@@3HA'), (4, True))
        self.assertEqual(msvcname.type_size('?d@@3NA'), (8, True))
        self.assertEqual(msvcname.type_size('?p@@3PBDB'), (4, False))
        self.assertEqual(msvcname.literal_bytes(LIT), (b'Hello\0', True))
        self.assertEqual(msvcname.literal_bytes('??_C@_0BA@ABCDEFGH@abc@')[1], False)
        self.assertEqual(msvcname.real_bytes('__real@3f800000'), struct.pack('<f', 1.0))
        self.assertEqual(msvcname.import_name('__imp__GetTickCount@0'), 'GetTickCount')

    def test_placement_and_relocations(self):
        self.assertEqual(self.img.read(0x01011000, 6), b'Hello\0')
        self.assertEqual(self.img.read32(0x01183010), 0x11223344)
        self.assertEqual(self.img.read32(0x01183014), 0x01011000)            # pointer to the literal
        self.assertEqual(self.img.read32(0x01012004), FN_F)                  # vftable slot -> function
        self.assertEqual(self.st(0x01012000), 2)                             # COL word: unresolved
        self.assertEqual(self.img.read(0x01012200, 4), struct.pack('<f', 1.0))
        self.assertEqual(self.st(0x01183010), 3)
        self.assertEqual(self.st(0x01183018), 0)                             # ?g_b: no address, not placed

    def test_ranks_and_conflicts(self):
        r = self.r
        self.assertEqual(r['copies_agreeing'], 1)                            # the literal twice
        self.assertGreaterEqual(r['overlaps_by_rank'].get('conflict 0-3', 0), 1)
        self.assertGreater(r['conflict_resolution'].get('lost_bytes', 0), 0)
        self.assertEqual(self.img.read(0x01011002, 2), b'll')                # the content-named literal wins

    def test_sections(self):
        s = self.r['sections']
        self.assertEqual(s['rdata'], ['0x00fdf000', '0x01183000'])
        self.assertEqual(s['data'][0], '0x01183000')
        self.assertEqual(s['tls'], ['0x01272000', '0x01273000'])
        names = [x.name for x in self.pe.sections]
        self.assertEqual(names, ['.text', '.rdata', '.data', '.tls', '.fnvsyn'])
        self.assertEqual(self.img.read32(0x01272004), 9)                     # TLS template
        self.assertEqual(self.st(0x011c4000), 4)                             # bss

    def test_classes(self):
        c = self.r['classes_by_section']
        self.assertEqual(c['bss'].get('init'), 1)                            # g_obj: mov ecx, offset g_obj
        self.assertEqual(c['rdata'].get('missing'), 1)                       # DAT_01012100
        self.assertEqual(c['rdata'].get('import'), 1)
        m = self.r['missing']
        self.assertEqual(m['missing_first_used_in_init_tree'], 1)
        self.assertEqual(m['first_consumers_in_order'][0]['func'], '0x%08x' % INIT)
        with open(os.path.join(self.tmp, 'm.tsv')) as f:
            rows = [x.split('\t') for x in f if not x.startswith('#')]
        self.assertEqual([(x[4], x[7]) for x in rows if x[5] == 'missing'], [('0x01012100', 'read')])

    def test_imports(self):
        imps = self.img.walk_imports()
        thunks = [x for x in imps if x[0] == 'thunk' and x[2]]
        self.assertEqual(len(thunks), 1)
        self.assertEqual(bytes(thunks[0][1]).lower(), b'kernel32.dll')
        self.assertEqual(bytes(thunks[0][2]), b'GetTickCount')
        self.assertEqual(thunks[0][6], 0x00fdf000)                          # bound into the original IAT slot

    def test_probes(self):
        p = self.r['probes']
        self.assertEqual(p.get('mismatch', 0), 0)
        self.assertEqual(p.get('match'), 1)

    def test_c_loader(self):
        lc = os.environ.get('FNVNX_LOADCHECK')
        if not lc:
            self.skipTest('set FNVNX_LOADCHECK to build/loader/loadcheck to run the C loader on the shell')
        r = subprocess.run([lc, '--exe', os.path.join(self.tmp, 's.exe')], capture_output=True, text=True)
        self.assertIn('pe_open=0', r.stdout)
        self.assertIn('imp_slots=1', r.stdout)
        self.assertIn('sections_mapped=4', r.stdout)


if __name__ == '__main__':
    unittest.main()
