#!/usr/bin/env python3
"""S3 data-image loader tests on a synthetic PE32 (no game content needed).

usage: python3 tests/s3/test_s3.py [-v]          (from ports/fnv-nx; output under build/s3, git-ignored)
env:   FNVNX_S3_OUT=dir  FNVNX_FUZZ_ITERS=N (default 1000)  CC=cc
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
LOADER = os.path.join(ROOT, 'loader')
OUT = os.path.abspath(os.environ.get('FNVNX_S3_OUT', os.path.join(ROOT, 'build', 's3')))
SYN = os.path.join(OUT, 'synth')
BIN = os.path.join(OUT, 'loader')
LOADCHECK = os.path.join(BIN, 'loadcheck')
sys.path.insert(0, os.path.join(LOADER, 'tools'))

from fnvnx_formats import AddrMap, CLASS_IDS, fixups_bytes, read_fixups  # noqa: E402
from fnvnx_pe import PE, Image, PEError  # noqa: E402
import scan_codeptrs  # noqa: E402

CFGS = {   # name -> (exe, hints, extra loadcheck args)
    'hints': ('plain.exe', True, []),
    'nohints': ('plain.exe', False, []),
    'reloc': ('reloc.exe', True, []),
}
MOVED = 0x02000000


def syn(name):
    return os.path.join(SYN, name)


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def rd(path, mode='rb'):
    with open(path, mode) as f:
        return f.read()


def wr(path, data):
    with open(path, 'wb' if isinstance(data, (bytes, bytearray)) else 'w') as f:
        f.write(data)


def setUpModule():
    os.makedirs(OUT, exist_ok=True)
    r = run(['make', '-s', '-C', LOADER, 'OUT=' + BIN])
    if r.returncode:
        raise RuntimeError('build failed:\n' + r.stdout + r.stderr)
    if os.path.isdir(SYN):
        shutil.rmtree(SYN)
    r = run([sys.executable, os.path.join(HERE, 'gen_synth_pe.py'), SYN])
    if r.returncode:
        raise RuntimeError('generator failed:\n' + r.stdout + r.stderr)


def read_expected(cfg):
    out = []
    for line in rd(syn('expected_%s.tsv' % cfg), 'r').splitlines():
        loc, tgt, k, c, aux = line.split('\t')
        out.append((int(loc, 16), int(tgt, 16), CLASS_IDS[k], int(c), int(aux)))
    return out


def py_scan(cfg, load_base=None):
    exe, hints, _ = CFGS[cfg]
    pe = PE(rd(syn(exe)))
    img = Image(pe, load_base)
    img.load_relocs()
    img.relocate()
    h = [int(x, 0) for x in rd(syn('hints.txt'), 'r').split()] if hints else []
    fixups, st = scan_codeptrs.Scanner(img, AddrMap.read_tsv(syn('addrmap.tsv')), h).scan()
    return pe, fixups, st


def loadcheck(cfg, *extra, exe=None, moved=False):
    exe_name, hints, more = CFGS[cfg]
    args = [LOADCHECK, '--exe', syn(exe or exe_name), '--addrmap', syn('addrmap.bin'), '--imports', syn('imports.txt'),
            '--probes', syn('probes.txt')] + more + list(extra)
    if '--no-data-probes' in args:
        args.remove('--no-data-probes')
    else:
        args += ['--data-probes', syn('dataprobes.txt')]
    if hints:
        args += ['--hints', syn('hints.txt')]
    if moved:
        args += ['--load-base', hex(MOVED), '--window', '0x%08x:0x%08x' % (MOVED, MOVED + 0x01400000)]
    # loadcheck exits early on purpose in the failure tests; leak reports would only change its exit code
    return run(args, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))


def stats_of(stdout):
    st = {}
    for line in stdout.splitlines():
        for tok in line.split():
            if '=' in tok:
                k, v = tok.split('=', 1)
                st[k] = v
    return st


class T01Python(unittest.TestCase):
    def test_scan_matches_construction(self):
        for cfg in CFGS:
            with self.subTest(cfg=cfg):
                _, fixups, _ = py_scan(cfg)
                self.assertEqual(fixups, read_expected(cfg))

    def test_vtable_listing(self):
        pe = PE(rd(syn('plain.exe')))
        img = Image(pe)
        h = [int(x, 0) for x in rd(syn('hints.txt'), 'r').split()]
        sc = scan_codeptrs.Scanner(img, AddrMap.read_tsv(syn('addrmap.tsv')), h)
        sc.scan()
        self.assertEqual([v[1:] for v in sc.vtables()],
                         [(1, 'col', '.?AVtype_info@@'), (3, 'col', '.?AVBase@@'), (4, 'col', '.?AVDerived@@'),
                          (2, 'hint', '')])

    def test_relocated_scan_gives_the_same_list(self):
        _, at_base, _ = py_scan('reloc')
        _, moved, _ = py_scan('reloc', MOVED)
        self.assertEqual(at_base, moved)

    def test_probes_and_wrapped(self):
        spec = json.loads(rd(syn('image_check.json'), 'r'))
        probes = [(int(va, 16), n, d) for va, n, d in spec['probes']]
        self.assertEqual(PE(rd(syn('plain.exe'))).check_probes(probes), (True, 3))
        self.assertEqual(PE(rd(syn('badprobe.exe'))).check_probes(probes), (False, 2))
        w = PE(rd(syn('wrapped.exe')))
        self.assertTrue(w.wrapped)
        self.assertFalse(w.check_probes(probes)[0])
        self.assertEqual((w.text_lo_rva, w.text_hi_rva), (PE(rd(syn('plain.exe'))).text_lo_rva,
                                                          PE(rd(syn('plain.exe'))).text_hi_rva))
        dp = [(int(l.split()[0], 16), bytes.fromhex(l.split()[1])) for l in rd(syn('dataprobes.txt'), 'r').splitlines()]
        self.assertEqual(Image(w).check_bytes(dp), (True, 7))
        enc = PE(rd(syn('wrapped_encdata.exe')))
        rdata = next(i for i, s in enumerate(enc.sections) if s.name == '.rdata')
        self.assertFalse(enc.section_stats(rdata)[3])
        self.assertTrue(w.section_stats(rdata)[3])

    def test_offline_tool_acceptance(self):
        tool = os.path.join(LOADER, 'tools', 'scan_codeptrs.py')
        common = ['--addrmap', syn('addrmap.tsv'), '--image-check', syn('image_check.json')]
        r = run([sys.executable, tool, syn('wrapped.exe')] + common + ['--data-probes', syn('dataprobes.txt'),
                '--vtables', os.path.join(OUT, 'vt.tsv'), '--vtable-slots', os.path.join(OUT, 'slots.tsv')])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(rd(os.path.join(OUT, 'slots.tsv'), 'r').splitlines()), 1 + 1 + 3 + 4 + 2)
        r = run([sys.executable, tool, syn('wrapped.exe')] + common)
        self.assertEqual(r.returncode, 4)
        self.assertIn('--data-probes', r.stderr)
        r = run([sys.executable, tool, syn('wrapped_encdata.exe')] + common + ['--data-probes', syn('dataprobes.txt')])
        self.assertEqual(r.returncode, 4)
        self.assertIn('not plain', r.stderr)

    def test_mkdataprobes(self):
        out = os.path.join(OUT, 'dp_from_units.txt')
        r = run([sys.executable, os.path.join(LOADER, 'tools', 'mkdataprobes.py'), syn('units_fake.json'), out])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(out, 'r').splitlines(), sorted(rd(syn('dataprobes.txt'), 'r').splitlines()))

    def test_s2_addrmap(self):
        s2 = AddrMap.read_s2_tsv(syn('addrmap_s2.tsv'))
        mine = AddrMap.read_tsv(syn('addrmap.tsv'))
        funcs = {a for a, (info, nat) in mine.entries.items() if info & 0xff == 1 and nat}
        self.assertEqual(set(s2.entries), funcs)
        self.assertEqual({a for a, (info, _) in s2.entries.items() if info & 0x800},
                         {a for a, (info, _) in mine.entries.items() if info & 0x800})
        merged = AddrMap.read_s2_tsv(syn('addrmap_s2.tsv')).merge(mine)
        self.assertEqual(set(merged.entries), set(mine.entries))

    def test_s2_addrmap_kinds(self):
        path = os.path.join(OUT, 'addrmap_s2_kinds.tsv')
        wr(path, '# fnvnx-addrmap 1\n# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\toffset\td1_symbol\t'
                 'd1_native\n'
                 '00401000\tF\t__fnv_00401000\t_Z1fv\ta.o\t\t\t\t\n'
                 '00401010\tG\t__fnv_00401010\t_ZN1CD0Ev\ta.o\tdeldtor\t\t__fnv_00401010_D1\t_ZN1CD1Ev\n'
                 '00401020\tE\t\t\t\tno native definition\t\t\t\n'
                 '01000100\tV\t_ZTV1C\t_ZTV1C\ta.o\tprimary\t8\t\t\n'
                 '01000120\tV\t_ZTV1C\t_ZTV1C\ta.o\tsecondary {for B} at +12\t28\t\t\n'
                 '01000140\tV\t_ZTV1D\t_ZTV1D\ta.o\tprimary (old six-column row)\n')
        m = AddrMap.read_s2_tsv(path)
        self.assertEqual(m.entries[0x00401000], (1, '__fnv_00401000'))
        self.assertEqual(m.entries[0x00401010], (1 | 0x800, '__fnv_00401010'))
        self.assertEqual(m.entries[0x00401020], (2, None))
        self.assertEqual(m.entries[0x01000100], (4, '_ZTV1C+8'))
        self.assertEqual(m.entries[0x01000120], (4, '_ZTV1C+28'))
        self.assertEqual(m.entries[0x01000140], (4, '_ZTV1D+8'))

    def test_mkprobes(self):
        hdr, txt = os.path.join(OUT, 'probes.h'), syn('probes.txt')
        r = run([sys.executable, os.path.join(LOADER, 'tools', 'mkprobes.py'), syn('image_check.json'), '--header', hdr,
                 '--txt', txt])
        self.assertEqual(r.returncode, 0, r.stderr)
        src = os.path.join(OUT, 'probes_use.c')
        wr(src, '#include "%s"\nunsigned n(void){return FNVNX_NPROBES + fnvnx_probes[0].size;}\n' % hdr)
        r = run([os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Werror', '-I' + os.path.join(LOADER, 'include'),
                 '-c', src, '-o', os.path.join(OUT, 'probes_use.o')])
        self.assertEqual(r.returncode, 0, r.stderr)


class T02Runtime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T01Python('test_mkprobes').test_mkprobes()      # probes.txt is needed by every loadcheck run

    def test_c_scanner_matches_python(self):
        for cfg in CFGS:
            for moved in ((False, True) if cfg == 'reloc' else (False,)):
                with self.subTest(cfg=cfg, moved=moved):
                    out = os.path.join(OUT, 'c_%s%s.bin' % (cfg, '_moved' if moved else ''))
                    r = loadcheck(cfg, '--fixups-out', out, moved=moved)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    pe, fixups, st = py_scan(cfg)
                    self.assertEqual(rd(out), fixups_bytes(pe, fixups))
                    cst = stats_of(r.stdout)
                    for k in ('scan_words', 'scan_textptrs', 'vtables_col', 'vtables_hint', 'vtable_slots',
                              'vptr_refs', 'fx_total'):
                        self.assertEqual(int(cst[k]), st[k], k)
                    # the plain searches (FNVNX_SCAN_NO_INDEX) give the same list and statistics as the indexed scan
                    out2 = out[:-4] + '_noindex.bin'
                    r2 = loadcheck(cfg, '--fixups-out', out2, '--no-index', moved=moved)
                    self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
                    self.assertEqual(rd(out2), rd(out))
                    self.assertEqual(stats_of(r2.stdout), cst)

    def test_pipeline_expectations(self):
        for cfg in CFGS:
            for trap in (False, True):
                for moved in ((False, True) if cfg == 'reloc' else (False,)):
                    name = 'expect_%s%s%s.txt' % (cfg, '_trap' if trap else '', '_moved' if moved else '')
                    with self.subTest(expect=name):
                        args = ['--expect', syn(name), '--headers'] + (['--trap'] if trap else [])
                        r = loadcheck(cfg, *args, moved=moved)
                        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                        self.assertIn('expect_failures=0', r.stdout)

    def test_tls_index_reset(self):
        # the TLS directory's AddressOfIndex holds 0xFFBF8C85 on disk (gen_synth_pe.py, like the real exe); the
        # loader stores 0, as Windows does for the main module (fs:[0x2c][_tls_index] has one slot in the port)
        for cfg, moved in (('hints', False), ('reloc', False), ('reloc', True)):
            with self.subTest(cfg=cfg, moved=moved):
                r = loadcheck(cfg, moved=moved)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                line = next((l for l in r.stdout.splitlines() if l.startswith('tls_index=')), '')
                st = stats_of(line)
                self.assertNotEqual(st.get('tls_index', '0x00000000'), '0x00000000', line)
                self.assertEqual((st.get('old'), st.get('now'), st.get('rc')), ('0xffbf8c85', '0x00000000', '0'), line)

    def test_map_code_read_only(self):
        # FNVNX_MAP_CODE (the loader-seam audit: .text readable as data, as on Windows): code sections map with the
        # file's bytes, read-only, and the code-pointer scan, imports and fixups come out exactly as without it
        for cfg, moved in (('hints', False), ('reloc', False), ('reloc', True)):
            with self.subTest(cfg=cfg, moved=moved):
                base = loadcheck(cfg, moved=moved)
                code = loadcheck(cfg, '--map-code', moved=moved)
                self.assertEqual(code.returncode, base.returncode, code.stdout + code.stderr)
                line = next((l for l in code.stdout.splitlines() if l.startswith('code_map=')), '')
                st = stats_of(line)
                self.assertTrue(int(st.get('code_map', '0')) >= 1 and st.get('match') == '1' and
                                st.get('prot_r') == '1', line)

                def same(out):   # the mapping count and its protect call are the only expected differences
                    return [l for l in out.splitlines() if not l.startswith('code_map=') and 'sections_mapped' not in l
                            and not l.startswith('protect_calls=')]
                self.assertEqual(same(code.stdout), same(base.stdout))
                pc = [int(stats_of(o).get('protect_calls', '0')) for o in (base.stdout, code.stdout)]
                self.assertEqual(pc[1], pc[0] + int(st['code_map']), pc)
                self.assertEqual(same(code.stderr), same(base.stderr))

    def test_precomputed_list(self):
        pe, fixups, _ = py_scan('hints')
        path = os.path.join(OUT, 'py_hints.bin')
        wr(path, fixups_bytes(pe, fixups))
        r = loadcheck('hints', '--fixups-in', path, '--expect', syn('expect_hints.txt'))
        # the scan statistics are absent with a precomputed list; everything else must hold
        fails = [l for l in r.stderr.splitlines() if l.startswith('FAIL') and 'stat ' not in l]
        stat_fails = [l for l in r.stderr.splitlines() if l.startswith('FAIL stat') and
                      not any(k in l for k in ('fx_', 'vtables_', 'vptr_refs'))]
        self.assertEqual(fails + stat_fails, [], r.stdout + r.stderr)

    def test_list_for_another_image_is_refused(self):
        pe, fixups, _ = py_scan('hints')
        b = bytearray(fixups_bytes(pe, fixups))
        struct.pack_into('<I', b, 16, pe.timestamp ^ 1)
        path = os.path.join(OUT, 'py_wrong.bin')
        wr(path, b)
        r = loadcheck('hints', '--fixups-in', path)
        self.assertEqual(r.returncode, 2)
        self.assertIn('does not fit this image', r.stderr)

    def test_stale_entries_are_not_written(self):
        pe, fixups, _ = py_scan('hints')
        fixups = list(fixups)
        i = next(k for k, f in enumerate(fixups) if f[2] == CLASS_IDS['isolated'])
        loc, tgt, k, c, aux = fixups[i]
        fixups[i] = (loc, tgt + 0x10, k, c, aux)           # the image no longer holds this target
        path = os.path.join(OUT, 'py_stale.bin')
        wr(path, fixups_bytes(pe, fixups))
        exp = os.path.join(OUT, 'expect_stale.txt')
        wr(exp, 'stat apply_mismatched 1\nword 0x%08x 0x%08x\n' % (loc, tgt))
        r = loadcheck('hints', '--fixups-in', path, '--expect', exp)
        self.assertIn('expect_failures=0', r.stdout, r.stderr)

    def test_wrapped_exe_and_probe_failures(self):
        r = loadcheck('hints', '--expect', syn('expect_wrapped.txt'), exe='wrapped.exe')   # Steam-like
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('verdict=data-checked', r.stdout)
        r = loadcheck('hints', '--no-data-probes', exe='wrapped.exe')
        self.assertEqual(r.returncode, 4)
        self.assertIn('--data-probes', r.stderr)
        r = loadcheck('hints', exe='wrapped_encdata.exe')
        self.assertEqual(r.returncode, 4)
        self.assertIn('not plain', r.stderr)
        r = loadcheck('hints', '--no-data-probes', exe='badprobe.exe')
        self.assertEqual(r.returncode, 4)
        self.assertIn('matched=2', r.stdout)
        r = loadcheck('hints', exe='plain.exe')
        self.assertIn('verdict=code-and-data-checked', r.stdout)

    def test_vtable_slot_reader(self):
        pe = PE(rd(syn('plain.exe')))
        img = Image(pe)
        sc = scan_codeptrs.Scanner(img, AddrMap.read_tsv(syn('addrmap.tsv')),
                                   [int(x, 0) for x in rd(syn('hints.txt'), 'r').split()])
        sc.scan()
        for va, n, how, _ in sc.vtables():
            r = loadcheck('hints', '--dump-vtable', hex(va))
            line = next(l for l in r.stdout.splitlines() if l.startswith('vtable '))
            c_slots = [int(x, 16) for x in line.split()[3:]]
            self.assertEqual(c_slots[:n], img.vtable_slots(va, n), hex(va))

    def test_headers_coexist_with_s2(self):
        s2h = os.path.join(ROOT, 'tools', 'link', 'fnvnx_addrmap.h')
        if not os.path.exists(s2h):
            self.skipTest('tools/link/fnvnx_addrmap.h (S2) not in this tree')
        src = os.path.join(OUT, 'both_headers.c')
        wr(src, '#include "%s"\n#include "fnvnx_loader.h"\n'
                'int f(fnvnx_codemap *m, const fnvnx_os *os) {\n'
                '  return fnvnx_codemap_from_s2(m, os, (const fnvnx_s2_addrmap_entry *)fnvnx_addrmap,\n'
                '                               fnvnx_addrmap_count, 0, 0, 0);\n}\n'
                'typedef char same_size[sizeof(struct fnvnx_addrmap_entry) == sizeof(fnvnx_s2_addrmap_entry) ? 1 : -1];\n'
                % s2h)
        r = run([os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Werror', '-I' + os.path.join(LOADER, 'include'),
                 '-c', src, '-o', os.path.join(OUT, 'both_headers.o')])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_c_units(self):
        exe = os.path.join(BIN, 'test_units')
        r = run([os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                 '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-I' + os.path.join(LOADER, 'include'),
                 os.path.join(HERE, 'test_units.c'), os.path.join(BIN, 'libfnvnx_loader.a'), '-o', exe])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run([exe])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unaligned_size_of_image(self):
        """SizeOfImage that ends mid-page (the GOG FalloutNV.exe: 0x010089e4) loads, rounded up as Windows does."""
        d = bytearray(rd(syn('plain.exe')))
        pe_off = struct.unpack_from('<I', d, 0x3c)[0]
        nsec, optsize = struct.unpack_from('<H', d, pe_off + 6)[0], struct.unpack_from('<H', d, pe_off + 20)[0]
        sh = pe_off + 24 + optsize + 40 * (nsec - 1)                      # the last section header
        vsize, rva = struct.unpack_from('<II', d, sh + 8)
        aligned = struct.unpack_from('<I', d, pe_off + 24 + 56)[0]
        struct.pack_into('<I', d, sh + 8, vsize - 0x1c)                  # its end moves into the page
        struct.pack_into('<I', d, pe_off + 24 + 56, rva + vsize - 0x1c)
        self.assertTrue((rva + vsize - 0x1c) & 0xfff)
        wr(syn('unaligned.exe'), d)
        self.assertEqual(PE(bytes(d)).size_of_image, aligned)
        r = loadcheck('hints', exe='unaligned.exe')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_window_and_protection(self):
        r = loadcheck('hints', '--window', '0x00400000:0x01200000')       # .tls/.bss/.rsrc do not fit
        self.assertEqual(r.returncode, 5)
        r = loadcheck('hints')
        self.assertEqual(stats_of(r.stdout).get('protect_calls'), '5')    # .rdata .data .tls .bss .rsrc

    def test_fuzz(self):
        exe = os.path.join(BIN, 'fuzz_loader')
        cc = os.environ.get('CC', 'cc')
        r = run([cc, '-std=c99', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                 '-I' + os.path.join(LOADER, 'include'), os.path.join(HERE, 'fuzz_loader.c'),
                 os.path.join(BIN, 'libfnvnx_loader.a'), '-o', exe])
        self.assertEqual(r.returncode, 0, r.stderr)
        iters = os.environ.get('FNVNX_FUZZ_ITERS', '1000')
        for seed_exe in ('plain.exe', 'reloc.exe'):
            r = run([exe, syn(seed_exe), syn('addrmap.bin'), iters, '7'])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
            print('\n  %s: %s' % (seed_exe, r.stdout.strip()), file=sys.stderr)

    def test_ilp32_build(self):
        if not shutil.which('aarch64-linux-gnu-gcc'):
            self.skipTest('no aarch64-linux-gnu-gcc')
        r = run(['make', '-s', '-C', LOADER, 'OUT=' + BIN, 'ilp32-check'])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
