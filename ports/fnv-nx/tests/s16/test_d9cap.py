#!/usr/bin/env python3
"""tests of d9cap, S16's D3D9 capture layer (tools/compare/d3d9/d9cap.c), under Wine. Synthetic data only.

    S16_E2E=<dir> python3 -I tests/s16/test_d9cap.py

The round trip: build.sh builds nxd9play.exe and d3d9.dll; nxd9play runs each synthetic capture (synth.py, plus an
extension-record capture written here) with d3d9.dll beside it (WINEDLLOVERRIDES d3d9=n,b), so its D3D9 calls go
through d9cap and come out as a journal; the journal then replays through plain nxd9play, and its frames must be
pixel-identical to the frames of the original capture. d9cap_probe.c checks forwarding, COM identity and wrapper
lifetime, and draws frame pairs that must replay alike (UP draws, partial locks, UpdateTexture, surface locks).

Skipped unless S16_E2E is set and wine, Xvfb and i686-w64-mingw32-gcc are installed. The shader tools (s6b_asm,
sdp2dksh) come from S16_E2E, else render/shaders/build/host; the Wine prefix from S16_PREFIX, else
/tmp/claude-0/s16/pfx, else <tmp>/pfx (created).
"""
import os, shutil, struct, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.abspath(os.path.join(HERE, '..', '..'))
TOOLS = os.path.join(PORT, 'tools', 'compare')
sys.path.insert(0, TOOLS)
import nxd9io, replay, synth

E2E = os.environ.get('S16_E2E')
HAVE = E2E and all(shutil.which(t) for t in ('wine', 'Xvfb', 'i686-w64-mingw32-gcc'))
CFLAGS = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-cast-function-type', '-Werror']
BASE_OVR = 'mscoree,mshtml=;winemenubuilder.exe=d'


def tool(name):
    for d in (E2E or '', os.path.join(PORT, 'render', 'shaders', 'build', 'host'),
              '/home/user/nx-agent/ports/fnv-nx/render/shaders/build/host'):
        p = os.path.join(d, name)
        if d and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def pngs(d):
    return sorted(f for f in os.listdir(d) if f.startswith('f') and f.endswith('.png') and '_d' not in f)


def pixels(p):
    from PIL import Image
    im = Image.open(p).convert('RGBA')
    return im.size, im.tobytes()


def ext_capture(path, sh):
    """S16's extension records and the paths synth does not take: a cube texture with two levels (TEXC per face),
    TEXU into a mip level, CFIL, STRC between a render-target texture and the back buffer, SFRQ, a DRAW"""
    s = synth.Scene(path, sh)
    s.tex(5, synth.A8R8G8B8, 64, 64, [], flags=1)
    s.w.u32('SURF', 7, 1, synth.A8R8G8B8, 64, 64, 5)
    lv = [synth.checker(16, 16, (255, 0, 0, 255), (0, 0, 255, 255), 2), synth.checker(8, 8, (0, 255, 0, 255), (255, 255, 0, 255), 1)]
    s.tex(6, synth.A8R8G8B8, 16, 16, lv)
    faces = [b''.join(bytes((f * 40, l * 100, 255 - f * 40, 255)) * ((16 >> l) ** 2) for f in range(6)) for l in range(2)]
    s.tex(9, synth.A8R8G8B8, 16, 16, faces, flags=2)
    s.w.rec('TEXC', struct.pack('<4I', 9, 3, 1, 256) + bytes(range(256)))   # face -Y, level 1 (8x8)
    s.w.rec('TEXU', struct.pack('<3I', 6, 1, 256) + bytes((i * 7) & 255 for i in range(256)))
    q = synth.quad_ct(-1, -1, 1, 1, 0.5, 0xFFFFFFFF) + synth.quad_ct(-0.8, -0.8, 0.0, 0.0, 0.5, 0xFFFFFFFF)
    s.vb(1, b''.join(q)); s.ib(1, synth.quad_ib(2))
    synth.common_ct(s, ps='ps3_tex')
    s.w.u32('STRM', 0, 1, 0, 24)
    s.w.u32('SFRQ', 0, 1)
    s.w.u32('STEX', 0, 6)
    s.ss(0, MIPFILTER=1, MINFILTER=1, MAGFILTER=1)
    s.clear(1, 0xFF102030)
    s.w.rec('CFIL', struct.pack('<2I4iI', 0, 1, 4, 4, 60, 40, 0xFF00FF00))
    s.w.u32('SDS ', 0)
    s.w.u32('SRT ', 0, 7)
    s.clear(1, 0xFF800000)
    s.w.rec('CFIL', struct.pack('<2I4iI', 7, 1, 8, 8, 24, 24, 0xFFFFFF00))
    s.w.u32('DRAW', 6, 4, 2)                                   # the second quad as a fan, non-indexed
    s.w.u32('SRT ', 0, 0)
    s.w.u32('SDS ', 0xFFFFFFFF)
    s.drwi(4, 4, 0, 2)
    s.w.rec('STRC', struct.pack('<2I4iI4iI', 7, 3, 0, 0, 64, 64, 0, 200, 100, 300, 200, 2))
    s.present()
    s.w.u32('SRT ', 0, 0)
    s.ss(0, MIPFILTER=0, MINFILTER=2, MAGFILTER=2)
    s.clear(1, 0xFF203040)
    s.drwi(0, 4, 0, 2)
    s.w.rec('STRC', struct.pack('<2I4iI4iI', 0, 1, 0, 0, 160, 120, 7, 0, 0, 0, 0, 1))
    s.w.u32('STEX', 0, 5)
    s.drwi(4, 4, 0, 2)
    s.present()
    s.close()


def texels(path):
    """{(texture, face, level): bytes} after every TEX/TEXU/TEXC of an NXD9 file. Textures are numbered in the order
    of their TEX records (the journal has its own ids); cube TEX levels are split into faces"""
    out, num = {}, {}
    for t, p in nxd9io.records(path):
        p = bytes(p)
        if t in ('TEX ', 'TEXU', 'TEXC'):
            (tid,) = struct.unpack_from('<I', p)
            if t == 'TEX ':
                num[tid] = len(num) + 1
            p = struct.pack('<I', num.get(tid, 0)) + p[4:]
        if t == 'TEX ':
            tid, fmt, w, h, lv, fl = struct.unpack_from('<6I', p)
            q = 24
            for l in range(lv):
                if q + 4 > len(p):   # render targets: no level entries
                    break
                (b,) = struct.unpack_from('<I', p, q)
                data = p[q + 4:q + 4 + b]
                if b:
                    if fl & 2:
                        for f in range(6): out[(tid, f, l)] = data[f * b // 6:(f + 1) * b // 6]
                    else:
                        out[(tid, 0, l)] = data
                q += 4 + ((b + 3) & ~3)
        elif t == 'TEXU':
            tid, l, b = struct.unpack_from('<3I', p)
            out[(tid, 0, l)] = p[12:12 + b]
        elif t == 'TEXC':
            tid, f, l, b = struct.unpack_from('<4I', p)
            out[(tid, f, l)] = p[16:16 + b]
    return out


@unittest.skipUnless(HAVE, 'S16_E2E=<dir> (and wine, Xvfb, i686-w64-mingw32-gcc) runs d9cap under Wine')
class D9Cap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = tempfile.mkdtemp(prefix='d9cap')
        t = cls.t
        asm, sdp = tool('s6b_asm'), tool('sdp2dksh')
        if not (asm and sdp):
            raise unittest.SkipTest('s6b_asm / sdp2dksh not found')
        r = subprocess.run([os.path.join(TOOLS, 'd3d9', 'build.sh'), f'{t}/bin'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        for d in ('plain', 'proxy'):
            os.makedirs(f'{t}/{d}')
            shutil.copy(f'{t}/bin/nxd9play.exe', f'{t}/{d}/')
        shutil.copy(f'{t}/bin/d3d9.dll', f'{t}/proxy/')
        r = subprocess.run(['i686-w64-mingw32-gcc'] + CFLAGS + ['-o', f'{t}/proxy/d9cap_probe.exe', os.path.join(HERE, 'd9cap_probe.c'),
                            '-ld3d9', '-lgdi32', '-luser32', '-luuid', '-static-libgcc'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.names = synth.build(asm, sdp, f'{t}/caps')
        with tempfile.TemporaryDirectory() as work:
            sh = synth.assemble(asm, sdp, work)
        ext_capture(f'{t}/caps/ext.nxd9', sh)
        cls.names.append('ext')
        for n in ('vs3_ct', 'ps3_vcolor', 'ps3_tex'):
            open(f'{t}/{n}.bin', 'wb').write(sh[n][0])
        pfx = os.environ.get('S16_PREFIX') or ('/tmp/claude-0/s16/pfx' if os.path.isfile('/tmp/claude-0/s16/pfx/system.reg') else f'{t}/pfx')
        cls.x = replay.Xvfb()
        cls.env = replay.wine_env(pfx, cls.x.display)
        if not os.path.isfile(os.path.join(pfx, 'system.reg')):
            subprocess.run(['wineboot', '-i'], env=cls.env, capture_output=True, timeout=600)
            subprocess.run(['wineserver', '-w'], env=cls.env, timeout=600)
        cls.res = {}
        for n in cls.names:
            cls.res[n] = (cls.play(f'{t}/caps/{n}.nxd9', f'{t}/ref/{n}'),
                          cls.play(f'{t}/caps/{n}.nxd9', f'{t}/prx/{n}', journal=f'{t}/j/{n}.nxd9'),
                          cls.play(f'{t}/j/{n}.nxd9', f'{t}/rep/{n}'))

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['wineserver', '-k'], env=cls.env, capture_output=True)
        cls.x.close()
        if not os.environ.get('S16_KEEP'):
            shutil.rmtree(cls.t, ignore_errors=True)

    @classmethod
    def run_wine(cls, exe, args, journal=None, extra_env=None):
        env = dict(cls.env)
        if journal:
            os.makedirs(os.path.dirname(journal), exist_ok=True)
            env.update(WINEDLLOVERRIDES=BASE_OVR + ';d3d9=n,b', S16_CAPTURE=replay.wine_path(journal))
        env.update(extra_env or {})
        for _ in range(2):   # a signal exit (Wine, at teardown) is retried once, as replay.py does
            r = subprocess.run(['wine', exe] + args, env=env, capture_output=True, text=True, timeout=300)
            if r.returncode >= 0:
                break
        return r

    @classmethod
    def play(cls, cap, out, journal=None, extra_env=None):
        os.makedirs(out, exist_ok=True)
        exe = f'{cls.t}/{"proxy" if journal else "plain"}/nxd9play.exe'
        return cls.run_wine(exe, [replay.wine_path(cap), '-o', replay.wine_path(out)], journal, extra_env)

    def test_round_trip_pixel_identical(self):
        t = self.t
        for n in self.names:
            ref, prx, rep = self.res[n]
            with self.subTest(capture=n):
                self.assertEqual(ref.returncode, 0, ref.stderr)
                self.assertEqual(prx.returncode, 0, prx.stderr)
                self.assertEqual(rep.returncode, 0, rep.stderr)
                self.assertIn('failed_calls 0', rep.stdout)
                self.assertIn('unjournaled 0', prx.stderr)
                frames = pngs(f'{t}/ref/{n}')
                self.assertEqual(len(frames), 2)
                self.assertEqual(pngs(f'{t}/rep/{n}'), frames)
                for f in frames:
                    a, b, c = (pixels(f'{t}/{s}/{n}/{f}') for s in ('ref', 'prx', 'rep'))
                    self.assertEqual(a, b, f'{n}/{f}: the proxy changed what nxd9play renders')
                    self.assertEqual(a, c, f'{n}/{f}: the journal replays differently')

    def test_journal_is_plain_nxd9(self):
        for n in self.names:
            j = f'{self.t}/j/{n}.nxd9'
            with self.subTest(capture=n):
                self.assertEqual(open(j, 'rb').read(8), nxd9io.MAGIC)
                s = nxd9io.stat(j)
                o = nxd9io.stat(f'{self.t}/caps/{n}.nxd9')
                self.assertEqual(s['frames'], 2)
                self.assertEqual(s['draws'], o['draws'])
                self.assertEqual(s['records'].get('DEV '), 1)
                for tag in ('VSH ', 'PSH ', 'DECL', 'VB  ', 'IB  ', 'CLR ', 'STRC', 'CFIL', 'SFRQ'):
                    self.assertEqual(s['records'].get(tag, 0), o['records'].get(tag, 0), tag)
                # shaders: same bytecode and FNV-1a 64, GLSL left empty
                for t_, p in nxd9io.records(j):
                    if t_ in ('VSH ', 'PSH '):
                        p = bytes(p)
                        _, lo, hi, nb = struct.unpack_from('<4I', p)
                        self.assertEqual(lo | hi << 32, nxd9io.fnv1a64(p[16:16 + nb]))
                        self.assertEqual(struct.unpack_from('<I', p, 16 + ((nb + 3) & ~3))[0], 0)

    def test_texels_journaled(self):
        # every level and cube face the capture uploads arrives in the journal with the same bytes
        for n in self.names:
            with self.subTest(capture=n):
                self.assertEqual(texels(f'{self.t}/j/{n}.nxd9'), texels(f'{self.t}/caps/{n}.nxd9'))
        self.assertIn((3, 3, 1), texels(f'{self.t}/j/ext.nxd9'))   # the cube's TEXC

    def test_frame_range(self):
        j = f'{self.t}/j/range.nxd9'
        r = self.play(f'{self.t}/caps/rtt.nxd9', f'{self.t}/range', journal=j, extra_env={'S16_CAPTURE_FRAMES': '1-1'})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('stopped: frame range', r.stderr)
        tags = [t_ for t_, _ in nxd9io.records(j)]
        self.assertEqual(tags.count('PRES'), 2)                       # frames 0 and 1, then the file ends
        self.assertEqual(tags[-1], 'PRES')
        first = tags[:tags.index('PRES')]
        self.assertFalse({'DRWI', 'DRAW', 'CLR '} & set(first))       # frame 0's pixel records are dropped
        self.assertTrue({'TEX ', 'SURF', 'VBU ', 'VSH ', 'RS  '} <= set(first))
        # a frame that does not build on the previous frame's pixels replays exactly (rtt's frame 1 does: it only
        # clears a viewport of its render target, so the rest of it is frame 0's drawing, which was dropped)
        j = f'{self.t}/j/range2.nxd9'
        r = self.play(f'{self.t}/caps/blend.nxd9', f'{self.t}/range2', journal=j, extra_env={'S16_CAPTURE_FRAMES': '1'})
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = self.play(j, f'{self.t}/range_rep')
        self.assertEqual(rep.returncode, 0, rep.stderr)
        self.assertEqual(pngs(f'{self.t}/range_rep'), ['f00000.png', 'f00001.png'])
        self.assertEqual(pixels(f'{self.t}/range_rep/f00001.png'), pixels(f'{self.t}/ref/blend/f00001.png'))

    def test_size_cap(self):
        j = f'{self.t}/j/cap.nxd9'
        r = self.play(f'{self.t}/caps/texture.nxd9', f'{self.t}/cap', journal=j, extra_env={'S16_CAPTURE_MAX_MB': '0.01'})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('stopped: size cap', r.stderr)
        self.assertLessEqual(os.path.getsize(j), int(0.01 * 1048576))
        list(nxd9io.records(j))                                       # whole records only

    def test_probe(self):
        t = self.t
        j = f'{t}/j/probe.nxd9'
        r = self.run_wine(f'{t}/proxy/d9cap_probe.exe', [replay.wine_path(f'{t}/{n}.bin') for n in ('vs3_ct', 'ps3_vcolor', 'ps3_tex')], journal=j)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('probe: ok', r.stdout)
        s = nxd9io.stat(j)
        self.assertEqual(s['frames'], 8)
        rep = self.play(j, f'{t}/probe_rep')
        self.assertEqual(rep.returncode, 0, rep.stderr)
        self.assertIn('frames 8 ', rep.stdout)
        self.assertIn('failed_calls 0', rep.stdout)
        img = [pixels(f'{t}/probe_rep/f{i:05d}.png') for i in range(8)]
        clear = bytes((0x30, 0x30, 0x30, 255)) * 64 * 64
        for i in (0, 2, 4):
            self.assertNotEqual(img[i][1], clear, f'frame {i} drew nothing')
        self.assertEqual(img[0], img[1], 'DrawPrimitiveUP against DrawPrimitive')
        self.assertEqual(img[2], img[3], 'DrawIndexedPrimitiveUP against DrawIndexedPrimitive')
        for i in (5, 6, 7):
            self.assertEqual(img[4], img[i], f'texture filled way {i - 4} against a whole lock')

    def test_without_proxy_no_journal(self):
        # the override is what loads d9cap: without it nxd9play gets Wine's d3d9 and nothing is written
        j = f'{self.t}/j/none.nxd9'
        env = {'S16_CAPTURE': replay.wine_path(j)}
        r = self.run_wine(f'{self.t}/proxy/nxd9play.exe', [replay.wine_path(f'{self.t}/caps/vcolor.nxd9'), '-o',
                          replay.wine_path(f'{self.t}/none')], extra_env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(j))


if __name__ == '__main__':
    unittest.main()
