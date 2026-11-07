#!/usr/bin/env python3
"""tests of T1-fastbench's reference capture cache: tools/compare/capstore.py (pack / unpack / has / export / verify)
and scene_stats.py --ref-cache (a cached reference is used without running nxd9play). Synthetic NXD9 only.

    python3 -I tests/t1/test_capstore.py
"""
import os, shutil, struct, subprocess, sys, tempfile, unittest
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'compare')
sys.path.insert(0, TOOLS)
import nxd9io, capstore

CS = [sys.executable, '-I', os.path.join(TOOLS, 'capstore.py')]


def synth(path, seed, shared, frames=2):
    """a capture: DEV, a texture shared with other captures (TEX + its TEXU, same bytes after the header), one of its
    own, odd-sized payloads (padding), state records and a draw per frame"""
    rng = np.random.default_rng(seed)
    w = nxd9io.Writer(path)
    w.u32('DEV ', 64, 32, 21, 0)
    w.rec('TEX ', struct.pack('<6I', 1, 21, 64, 64, 1, 0) + b'\0' * 8 + shared)
    w.rec('TEXU', struct.pack('<8I', 1, 0, 0, 0, 0, 0, 0, 0) + shared)
    w.rec('TEX ', struct.pack('<6I', 2, 21, 32, 32, 1, 0) + b'\0' * 8 + rng.bytes(5001))
    w.rec('VBU ', rng.bytes(4095))                      # under MIN: stays in the skeleton
    w.rec('VBU ', rng.bytes(4097))
    for f in range(frames):
        for k in range(50):
            w.u32('RS  ', k, int(rng.integers(0, 9)))
        w.u32('STEX', 0, 1)
        w.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, 3, 0, 1))
        w.rec('PRES')
    w.close()


class Store(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix='t1cap.')
        self.store = os.path.join(self.t, 'store')
        self.shared = np.random.default_rng(99).bytes(70000)

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def cap(self, name, seed):
        p = os.path.join(self.t, name + '.nxd9'); synth(p, seed, self.shared); return p

    def test_roundtrip_dedup_has(self):
        a, b = self.cap('a', 1), self.cap('b', 2)
        ra = capstore.pack(self.store, 'a', a, {'scene': 'h1'})
        rb = capstore.pack(self.store, 'b', b, {'scene': 'h2'})
        self.assertEqual(ra['new_blobs'], 3)        # shared texture once (TEX and TEXU), own texture, the 4097 VBU
        self.assertEqual(rb['new_blobs'], 2)        # the shared one is already there
        for sid, src in (('a', a), ('b', b)):
            out = os.path.join(self.t, sid + '.out')
            subprocess.run(CS + ['unpack', self.store, sid, out], check=True)
            self.assertEqual(open(out, 'rb').read(), open(src, 'rb').read())
        self.assertEqual(subprocess.run(CS + ['has', self.store, 'a', '--scene-hash', 'h1']).returncode, 0)
        self.assertEqual(subprocess.run(CS + ['has', self.store, 'a', '--scene-hash', 'h2']).returncode, 1)
        self.assertEqual(subprocess.run(CS + ['unpack', self.store, 'zz', os.path.join(self.t, 'z')],
                                        stderr=subprocess.DEVNULL).returncode, 3)
        self.assertEqual(subprocess.run(CS + ['verify', self.store], capture_output=True).returncode, 0)

    def test_corrupt_blob_detected(self):
        capstore.pack(self.store, 'a', self.cap('a', 1), {})
        own = [h for h in open(os.path.join(self.store, 'index', 'a.blobs')).read().split()]
        import lzma
        bp = capstore.blob_path(self.store, own[-1])
        data = bytearray(lzma.decompress(open(bp, 'rb').read())); data[10] ^= 1
        open(bp, 'wb').write(lzma.compress(bytes(data)))
        r = subprocess.run(CS + ['unpack', self.store, 'a', os.path.join(self.t, 'a.out')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn('sha256', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.t, 'a.out')))

    def test_export_is_a_complete_store(self):
        a, b = self.cap('a', 1), self.cap('b', 2)
        capstore.pack(self.store, 'a', a, {}); capstore.pack(self.store, 'b', b, {})
        os.makedirs(os.path.join(self.store, 'ref', 'b', 'ref')); open(os.path.join(self.store, 'ref', 'b', 'rc'), 'w').write('0\n')
        ex = os.path.join(self.t, 'ex')
        subprocess.run(CS + ['export', self.store, ex, 'b', 'missing'], check=True, capture_output=True)
        self.assertFalse(os.path.exists(os.path.join(ex, 'index', 'a.tsv')))
        self.assertTrue(os.path.isfile(os.path.join(ex, 'ref', 'b', 'rc')))
        out = os.path.join(self.t, 'b.out')
        subprocess.run(CS + ['unpack', ex, 'b', out], check=True)
        self.assertEqual(open(out, 'rb').read(), open(b, 'rb').read())

    def test_repack_voids_reference(self):
        a = self.cap('a', 1)
        capstore.pack(self.store, 'a', a, {})
        os.makedirs(os.path.join(self.store, 'ref', 'a')); open(os.path.join(self.store, 'ref', 'a', 'rc'), 'w').write('0\n')
        capstore.pack(self.store, 'a', a, {})
        self.assertFalse(os.path.exists(os.path.join(self.store, 'ref', 'a')))


class RefCache(unittest.TestCase):
    """scene_stats --ref-cache: a filled cache is used as is (the --exe given does not exist: running it would fail)"""
    def test_cached_reference_used(self):
        from PIL import Image
        t = tempfile.mkdtemp(prefix='t1ref.')
        try:
            cap = os.path.join(t, 'scn.nxd9'); synth(cap, 5, b'x' * 5000, frames=2)
            png = os.path.join(t, 'f.png'); Image.fromarray(np.full((32, 64, 3), 77, np.uint8), 'RGB').save(png)
            rc = os.path.join(t, 'rc'); os.makedirs(os.path.join(rc, 'ref', 'scn'))
            for i in range(2):
                shutil.copy(png, os.path.join(rc, 'ref', 'scn', f'f{i:05d}.png'))
            open(os.path.join(rc, 'ref', 'scn', 'frames.tsv'), 'w').write('0\t5.0\n1\t6.0\n')
            open(os.path.join(rc, 'cov.tsv'), 'w').write('1\t0\t0\t0\t0\t2048\n')
            open(os.path.join(rc, 'rc'), 'w').write('0\n')
            fake = os.path.join(t, 'nx_render')
            open(fake, 'w').write('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && o=$2; shift; done\n'
                                  f'mkdir -p "$o"; cp {png} "$o/f00000.png"; cp {png} "$o/f00001.png"; '
                                  'echo "draws 2 skipped 0" > "$o/summary.txt"\n')
            os.chmod(fake, 0o755)
            sdp = os.path.join(t, 'sdp'); os.makedirs(sdp)
            out = os.path.join(t, 'out.json')
            r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'scene_stats.py'), cap, '--exe',
                                os.path.join(t, 'absent.exe'), '--prefix', os.path.join(t, 'pfx'), '--nx-render', fake,
                                '--shaders', sdp, '--sdp-dir', sdp, '--out', out, '--ref-cache', rc],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            import json
            j = json.load(open(out))
            self.assertTrue(j.get('ref_cached'))
            self.assertEqual(j['ref']['frames_rendered'], 2); self.assertEqual(j['ref']['coverage_pixels'], 2048)
            self.assertEqual(j['paths']['translated']['frames_over'], 0)
        finally:
            shutil.rmtree(t, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
